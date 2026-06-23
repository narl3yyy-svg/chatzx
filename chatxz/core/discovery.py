import base64
import json
import time
import RNS

from chatxz.core.lan_rns import register_udp_peer_ip

def discovery_timeout_s():
    try:
        from chatxz.utils.platform import is_android
        if is_android():
            return 300
    except Exception:
        pass
    return 60


DISCOVERY_TIMEOUT = 60
APP_NAME = "chatxz"
PUBKEY_SIZE = RNS.Identity.KEYSIZE // 8


def normalize_hash(h):
    return (h or "").replace("<", "").replace(">", "").replace(":", "").strip().lower()


def register_identity_from_beacon(data):
    """Cache peer identity from beacon pubkey so connect works without RNS announce."""
    if not data:
        return False
    pubkey_b64 = data.get("pubkey")
    if not pubkey_b64:
        return False
    try:
        pubkey = base64.b64decode(pubkey_b64, validate=True)
    except Exception:
        return False
    if len(pubkey) != PUBKEY_SIZE:
        return False

    dest_hex = normalize_hash(data.get("hash"))
    if len(dest_hex) != 32:
        return False
    try:
        dest_bytes = bytes.fromhex(dest_hex)
    except ValueError:
        return False

    app_data = None
    name = (data.get("name") or "").strip()
    if name:
        try:
            app_data = json.dumps({"app": APP_NAME, "name": name}).encode("utf-8")
        except Exception:
            app_data = None

    identity_hex = normalize_hash(data.get("identity_hash"))
    packet_bytes = dest_bytes
    if identity_hex and len(identity_hex) == 32:
        try:
            packet_bytes = bytes.fromhex(identity_hex)
        except ValueError:
            packet_bytes = dest_bytes

    try:
        RNS.Identity.remember(packet_bytes, dest_bytes, pubkey, app_data)
        return True
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
    if not ident or not getattr(ident, "hash", None):
        return ""
    try:
        hash_input = ident.hash + APP_NAME.encode("utf-8") + b"messages"
        return normalize_hash(RNS.hexrep(RNS.Identity.full_hash(hash_input)))
    except Exception:
        return ""


class AnnounceHandler:
    aspect_filter = None

    def __init__(self, discovery):
        self.discovery = discovery

    def received_announce(self, destination_hash, announced_identity, app_data):
        self.discovery._on_announce(destination_hash, app_data, announced_identity)


class PeerDiscovery:
    def __init__(self, on_peer_seen=None):
        self.peers = {}
        self.running = False
        self._handler = None
        self.on_peer_seen = on_peer_seen
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

    def _store_peer(self, peer):
        hash_hex = normalize_hash(peer.get("hash"))
        if not hash_hex:
            return
        peer["hash"] = hash_hex
        existing = self.peers.get(hash_hex)
        if existing:
            if peer.get("ip") and not existing.get("ip"):
                existing["ip"] = peer["ip"]
                existing["port"] = peer.get("port", 8742)
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
        self._store_peer(peer)
        self._log_once(hash_hex, f"[discovery] RNS peer discovered: {name or hash_hex[:12]}...")

    def _on_beacon(self, data, my_dest_hash, my_identity_hash=None):
        if not self.running or not self.accept_peers:
            return False
        if data.get("app") != APP_NAME:
            return False
        hash_hex = normalize_hash(data.get("hash"))
        my_dest = normalize_hash(my_dest_hash)
        my_ident = normalize_hash(my_identity_hash or my_dest_hash)
        identity_hex = normalize_hash(data.get("identity_hash"))
        if identity_hex and (identity_hex == my_ident or identity_hex == my_dest):
            return False
        if not hash_hex or hash_hex == my_dest or hash_hex == my_ident:
            return False
        name = data.get("name", "") or hash_hex[:8]
        peer = {
            "hash": hash_hex,
            "name": name,
            "app": APP_NAME,
            "ip": data.get("ip"),
            "port": data.get("port", 8742),
            "last_seen": time.time(),
            "via": "beacon",
        }
        if identity_hex and identity_hex != hash_hex:
            peer["identity_hash"] = identity_hex
        if data.get("pubkey"):
            peer["pubkey"] = data.get("pubkey")
        if register_identity_from_beacon(data):
            peer["via"] = "rns"
            self._log_once(
                f"beacon-id:{hash_hex}",
                f"[discovery] Beacon identity registered: {name} ({data.get('ip', '?')})",
            )
        self._store_peer(peer)
        self._log_once(
            f"beacon:{hash_hex}",
            f"[discovery] Beacon peer discovered: {name} ({data.get('ip', '?')})",
        )
        return True

    def _log_once(self, key, message, interval=30):
        now = time.time()
        if now - self._last_log.get(key, 0) < interval:
            return
        self._last_log[key] = now
        print(message)

    def _peer_rank(self, peer):
        score = 0
        if peer.get("via") == "rns":
            score += 10
        if len(normalize_hash(peer.get("hash"))) >= 32:
            score += 5
        if peer.get("ip"):
            score += 2
        name = peer.get("name", "")
        if name and name != normalize_hash(peer.get("hash"))[:8]:
            score += 1
        return score

    def get_peers(self):
        if not self.accept_peers:
            return []
        now = time.time()
        ttl = discovery_timeout_s()
        stale = [h for h, p in self.peers.items() if now - p["last_seen"] > ttl]
        for h in stale:
            del self.peers[h]

        deduped = {}
        for peer in self.peers.values():
            ip = peer.get("ip")
            key = f"{ip}:{peer.get('port', 8742)}" if ip else peer["hash"]
            existing = deduped.get(key)
            if not existing or self._peer_rank(peer) > self._peer_rank(existing):
                deduped[key] = peer
        return list(deduped.values())
