import base64
import json
import time
import RNS

from chatxz.core.lan_rns import register_udp_peer_ip
from chatxz.utils.lan_scope import same_lan_scope

def discovery_timeout_s():
    return 300


DISCOVERY_TIMEOUT = 300
APP_NAME = "chatxz"
PUBKEY_SIZE = RNS.Identity.KEYSIZE // 8


def normalize_hash(h):
    return (h or "").replace("<", "").replace(">", "").replace(":", "").strip().lower()


def register_identity_from_beacon(data):
    """Cache peer identity from beacon pubkey so connect works without RNS announce."""
    try:
        from chatxz.core.peer_identity import register_beacon_identity
        return bool(register_beacon_identity(data))
    except Exception:
        return False


def register_identity_from_peer(peer):
    """Register RNS identity from a discovery peer record (beacon or RNS)."""
    if not peer:
        return False
    if peer.get("pubkey"):
        return register_identity_from_beacon(peer)
    return False


def message_dest_hash_for_identity(ident):
    """32-char RNS destination hash (must match Destination.hash / announces)."""
    if not ident or not getattr(ident, "hash", None):
        return ""
    try:
        dest_hash = RNS.Destination.hash(ident, APP_NAME, "messages")
        return normalize_hash(dest_hash.hex())
    except Exception:
        return ""


class AnnounceHandler:
    aspect_filter = None

    def __init__(self, discovery):
        self.discovery = discovery

    def received_announce(self, destination_hash, announced_identity, app_data):
        self.discovery._on_announce(destination_hash, app_data, announced_identity)


class PeerDiscovery:
    def __init__(self, on_peer_seen=None, on_peer_evicted=None):
        self.peers = {}
        self.running = False
        self._handler = None
        self.on_peer_seen = on_peer_seen
        self.on_peer_evicted = on_peer_evicted
        self._last_log = {}
        self.accept_peers = False

    def start(self):
        self.running = True
        self.accept_peers = False
        self._handler = AnnounceHandler(self)
        RNS.Transport.register_announce_handler(self._handler)
        print("[discovery] Announce handler registered (tap Announce to discover peers)")

    def stop(self):
        self.running = False

    def enable_discovery(self, clear=False):
        """Show LAN peers only after the user taps Announce."""
        self.accept_peers = True
        if clear:
            self.clear_peers()

    def disable_discovery(self):
        self.accept_peers = False
        self.clear_peers()

    def clear_peers(self):
        self.peers.clear()
        self._last_log.clear()

    def purge_hashes(self, hashes):
        """Remove stale discovery entries by destination or identity hash."""
        targets = {
            normalize_hash(h) for h in (hashes or []) if normalize_hash(h)
        }
        if not targets:
            return 0
        removed = 0
        for key in list(self.peers.keys()):
            peer = self.peers.get(key) or {}
            peer_hashes = {
                normalize_hash(key),
                normalize_hash(peer.get("hash")),
                normalize_hash(peer.get("identity_hash")),
            }
            if peer_hashes & targets:
                del self.peers[key]
                removed += 1
        for target in targets:
            self._last_log.pop(target, None)
            self._last_log.pop(f"beacon:{target}", None)
            self._last_log.pop(f"beacon-id:{target}", None)
        return removed

    def _attach_peer_ip(self, peer):
        if (peer.get("ip") or "").strip():
            return peer
        name = (peer.get("name") or "").strip()
        ident = normalize_hash(peer.get("identity_hash"))
        for existing in self.peers.values():
            if not existing.get("ip"):
                continue
            if name and existing.get("name") == name:
                peer["ip"] = existing["ip"]
                peer["port"] = existing.get("port", 8742)
                return peer
            if ident and normalize_hash(existing.get("identity_hash")) == ident:
                peer["ip"] = existing["ip"]
                peer["port"] = existing.get("port", 8742)
                return peer
        return peer

    def _evict_superseded_peers(self, peer):
        """Drop older entries for the same host when identity/hash changes."""
        peer = self._attach_peer_ip(dict(peer))
        ip = (peer.get("ip") or "").strip()
        new_hash = normalize_hash(peer.get("hash"))
        new_ident = normalize_hash(peer.get("identity_hash"))
        new_pubkey = peer.get("pubkey")
        name = (peer.get("name") or "").strip()
        removed = []
        for key, existing in list(self.peers.items()):
            same_host = ip and existing.get("ip") == ip
            same_ident = (
                new_ident
                and normalize_hash(existing.get("identity_hash")) == new_ident
            )
            same_pubkey = new_pubkey and existing.get("pubkey") == new_pubkey
            same_hash = normalize_hash(existing.get("hash")) == new_hash
            existing_name = (existing.get("name") or "").strip()
            same_name = (
                name
                and existing_name
                and name == existing_name
                and name != new_hash[:8]
                and existing_name != normalize_hash(existing.get("hash"))[:8]
            )
            if same_hash:
                continue
            if same_host or same_ident or same_pubkey or same_name:
                removed.append(normalize_hash(existing.get("hash")) or key)
                del self.peers[key]
        return removed, peer

    def _notify_peer_evicted(self, removed_hashes, new_peer):
        if not removed_hashes or not self.on_peer_evicted:
            return
        try:
            self.on_peer_evicted(removed_hashes, new_peer)
        except Exception as e:
            print(f"[discovery] on_peer_evicted error: {e}")

    def register_link_peer(self, peer_hash, name="", via="link"):
        """Register a peer learned from an established RNS link (e.g. TCP hub)."""
        hash_hex = normalize_hash(peer_hash)
        if not hash_hex:
            return
        self._store_peer({
            "hash": hash_hex,
            "name": (name or hash_hex[:8]).strip() or hash_hex[:8],
            "via": via,
            "last_seen": time.time(),
        })

    def _store_peer(self, peer):
        hash_hex = normalize_hash(peer.get("hash"))
        if not hash_hex:
            return
        peer["hash"] = hash_hex
        removed, peer = self._evict_superseded_peers(peer)
        if removed:
            self._notify_peer_evicted(removed, peer)
        existing = self.peers.get(hash_hex)
        if existing:
            new_ip = (peer.get("ip") or "").strip()
            if new_ip and new_ip != (existing.get("ip") or "").strip():
                existing["ip"] = new_ip
                existing["port"] = peer.get("port", existing.get("port", 8742))
            elif peer.get("ip") and not existing.get("ip"):
                existing["ip"] = peer["ip"]
                existing["port"] = peer.get("port", 8742)
            if peer.get("identity_hash") and not existing.get("identity_hash"):
                existing["identity_hash"] = peer["identity_hash"]
            if peer.get("pubkey") and not existing.get("pubkey"):
                existing["pubkey"] = peer["pubkey"]
            if peer.get("name") and peer["name"] != hash_hex[:8]:
                existing["name"] = peer["name"]
            if peer.get("via") != "beacon" or existing.get("via") == "beacon":
                existing["via"] = peer.get("via", existing.get("via"))
            existing["last_seen"] = peer.get("last_seen", time.time())
            peer = existing
        else:
            self.peers[hash_hex] = peer
        if peer.get("ip"):
            register_udp_peer_ip(peer["ip"])
        if self.on_peer_seen:
            try:
                self.on_peer_seen(peer)
            except Exception as e:
                print(f"[discovery] on_peer_seen error: {e}")

    def _on_announce(self, destination_hash, app_data, announced_identity=None):
        if not self.running or not self.accept_peers:
            return

        hash_hex = normalize_hash(RNS.hexrep(destination_hash))
        identity_hex = ""
        if announced_identity and hasattr(announced_identity, "hash"):
            identity_hex = normalize_hash(RNS.hexrep(announced_identity.hash))
        name = ""
        app_name = ""

        if app_data:
            try:
                data = json.loads(app_data.decode("utf-8"))
                app_name = data.get("app", "")
                name = data.get("name", "")
            except Exception:
                pass

        if app_name != APP_NAME:
            return

        peer = {
            "hash": hash_hex,
            "name": name or hash_hex[:8],
            "app": app_name,
            "last_seen": time.time(),
            "via": "rns",
        }
        if identity_hex and identity_hex != hash_hex:
            peer["identity_hash"] = identity_hex
        if announced_identity:
            try:
                peer["pubkey"] = base64.b64encode(
                    announced_identity.get_public_key()
                ).decode("ascii")
            except Exception:
                pass
        self._store_peer(peer)
        via = peer.get("via", "rns")
        self._log_once(
            hash_hex,
            f"[discovery] RNS peer discovered ({via}): {name or hash_hex[:12]}...",
        )

    def _on_beacon(self, data, my_dest_hash, my_identity_hash=None, source_ip=None):
        if not self.running or not self.accept_peers:
            return False
        if data.get("app") != APP_NAME:
            return False
        my_dest = normalize_hash(my_dest_hash)
        my_ident = normalize_hash(my_identity_hash or my_dest_hash)
        identity_hex = normalize_hash(data.get("identity_hash"))
        if identity_hex and (identity_hex == my_ident or identity_hex == my_dest):
            return False
        try:
            from chatxz.core.peer_identity import peer_record_from_beacon
            peer = peer_record_from_beacon(data)
        except Exception:
            peer = None
        if not peer:
            return False
        hash_hex = normalize_hash(peer.get("hash"))
        if not hash_hex or hash_hex == my_dest or hash_hex == my_ident:
            return False
        if identity_hex and identity_hex == my_ident:
            return False
        peer["last_seen"] = time.time()
        peer_ip = (data.get("ip") or peer.get("ip") or "").strip()
        source = (source_ip or "").strip()
        try:
            from chatxz.utils.platform import lan_ip
            local_ip = (lan_ip() or "").strip()
        except Exception:
            local_ip = ""
        if local_ip:
            if peer_ip and not same_lan_scope(peer_ip, local_ip):
                if source and same_lan_scope(source, local_ip):
                    peer_ip = source
                else:
                    return False
            elif not peer_ip and source and not same_lan_scope(source, local_ip):
                return False
        peer["ip"] = peer_ip or source or peer.get("ip")
        peer["port"] = data.get("port", peer.get("port", 8742))
        name = peer.get("name") or hash_hex[:8]
        peer["name"] = name
        if register_identity_from_beacon(data):
            peer["via"] = "rns"
            self._log_once(
                f"beacon-id:{hash_hex}",
                f"[discovery] Beacon identity registered: {name} ({peer.get('ip', '?')})",
            )
        self._store_peer(peer)
        self._log_once(
            f"beacon:{hash_hex}",
            f"[discovery] Beacon peer discovered: {name} ({peer.get('ip', '?')})",
        )
        return True

    def _log_once(self, key, message, interval=5):
        now = time.time()
        if now - self._last_log.get(key, 0) < interval:
            return
        self._last_log[key] = now
        print(message)

    @staticmethod
    def _same_peer_identity(a, b):
        """True when two discovery records refer to the same RNS identity."""
        a_ident = normalize_hash(a.get("identity_hash"))
        b_ident = normalize_hash(b.get("identity_hash"))
        if a_ident and b_ident and a_ident == b_ident:
            return True
        a_key = a.get("pubkey")
        b_key = b.get("pubkey")
        return bool(a_key and b_key and a_key == b_key)

    def _peer_rank(self, peer):
        score = 0
        if peer.get("via") == "rns":
            score += 20
        if peer.get("pubkey"):
            score += 8
        if len(normalize_hash(peer.get("hash"))) >= 32:
            score += 5
        if peer.get("ip"):
            score += 2
        name = peer.get("name", "")
        hash_prefix = normalize_hash(peer.get("hash"))[:8]
        if name and name != hash_prefix and not name.startswith(hash_prefix):
            score += 3
        return score

    @staticmethod
    def _same_subnet(ip_a, ip_b):
        if not ip_a or not ip_b:
            return True
        return same_lan_scope(ip_a, ip_b)

    def _same_ip_preferred(self, a, b):
        """Pick the better peer record when two entries share an IP."""
        a_verified = bool(a.get("pubkey"))
        b_verified = bool(b.get("pubkey"))
        if a_verified != b_verified:
            return a if a_verified else b
        a_name = (a.get("name") or "").strip()
        b_name = (b.get("name") or "").strip()
        a_hash = normalize_hash(a.get("hash"))
        b_hash = normalize_hash(b.get("hash"))
        same_name = (
            a_name
            and b_name
            and a_name == b_name
            and a_name != a_hash[:8]
            and b_name != b_hash[:8]
        )
        if same_name:
            return a if a.get("last_seen", 0) >= b.get("last_seen", 0) else b
        rank_a = self._peer_rank(a)
        rank_b = self._peer_rank(b)
        if rank_a != rank_b:
            return a if rank_a > rank_b else b
        return a if a.get("last_seen", 0) >= b.get("last_seen", 0) else b

    @staticmethod
    def _peer_dedup_key(peer):
        ident = normalize_hash(peer.get("identity_hash"))
        if ident:
            return f"ident:{ident}"
        pubkey = (peer.get("pubkey") or "").strip()
        if pubkey:
            return f"pk:{pubkey[:48]}"
        return f"hash:{normalize_hash(peer.get('hash'))}"

    def get_peers(self, scope_ip=None):
        if not self.accept_peers:
            return []
        now = time.time()
        ttl = discovery_timeout_s()
        stale = [h for h, p in self.peers.items() if now - p["last_seen"] > ttl]
        for h in stale:
            del self.peers[h]

        deduped = {}
        for peer in self.peers.values():
            if scope_ip and peer.get("ip") and not self._same_subnet(peer["ip"], scope_ip):
                continue
            key = self._peer_dedup_key(peer)
            existing = deduped.get(key)
            if not existing:
                deduped[key] = peer
                continue
            existing_hash = normalize_hash(existing.get("hash"))
            peer_hash = normalize_hash(peer.get("hash"))
            if existing_hash != peer_hash:
                if self._same_peer_identity(existing, peer):
                    peer_score = self._peer_rank(peer)
                    existing_score = self._peer_rank(existing)
                    if peer_score > existing_score:
                        deduped[key] = peer
                    elif (
                        peer_score == existing_score
                        and peer.get("last_seen", 0) >= existing.get("last_seen", 0)
                    ):
                        deduped[key] = peer
                    continue
                existing_verified = bool(existing.get("pubkey"))
                peer_verified = bool(peer.get("pubkey"))
                if peer_verified and not existing_verified:
                    deduped[key] = peer
                elif existing_verified and not peer_verified:
                    pass
                elif peer.get("last_seen", 0) >= existing.get("last_seen", 0):
                    deduped[key] = peer
                continue
            peer_score = self._peer_rank(peer)
            existing_score = self._peer_rank(existing)
            if peer_score > existing_score:
                deduped[key] = peer
            elif peer_score == existing_score and peer.get("last_seen", 0) >= existing.get("last_seen", 0):
                deduped[key] = peer
        collapsed = {}
        no_ip_peers = []
        for peer in deduped.values():
            ip = (peer.get("ip") or "").strip()
            if not ip:
                no_ip_peers.append(peer)
                continue
            existing = collapsed.get(ip)
            if not existing:
                collapsed[ip] = peer
                continue
            collapsed[ip] = self._same_ip_preferred(peer, existing)
        return no_ip_peers + list(collapsed.values())

    def current_hashes(self):
        hashes = set()
        for peer in self.get_peers():
            for key in ("hash", "identity_hash"):
                clean = normalize_hash(peer.get(key))
                if clean:
                    hashes.add(clean)
        return hashes

    def peer_is_current(self, peer_hash):
        clean = normalize_hash(peer_hash)
        if not clean:
            return False
        return clean in self.current_hashes()
