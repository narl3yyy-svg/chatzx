import base64
import json
import time
import RNS

from chatxz.core.lan_rns import (
    announce_packet_receiving_interface,
    interface_family,
    register_udp_peer_ip,
    serial_interface_online,
    unregister_udp_peer_ip,
)
from chatxz.core.rns_interfaces import configured_serial_enabled, load_settings_interfaces
from chatxz.utils.lan_scope import peer_in_scope, same_lan_scope


def serial_discovery_active():
    """True when USB serial is configured and the RNS SerialInterface is online."""
    try:
        interfaces = load_settings_interfaces()
        return (
            configured_serial_enabled(interfaces)
            and serial_interface_online() is not None
        )
    except Exception:
        return False

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

    def _scope_ip(self):
        """Pinned/auto LAN IPv4 used for discovery isolation (None when unscoped)."""
        try:
            from chatxz.utils.platform import discovery_scope_ip

            return (discovery_scope_ip() or "").strip() or None
        except Exception:
            return None

    def _sanitize_peer_scope(self, peer):
        """Drop cross-subnet LAN IPs when serial bridge is active; return False to reject."""
        candidate = dict(peer or {})
        if (candidate.get("via") or "").strip() == "serial":
            if not serial_discovery_active():
                return None
            candidate.pop("ip", None)
            return candidate
        scope = self._scope_ip()
        if not scope:
            return candidate
        ip = (candidate.get("ip") or "").strip()
        if not ip:
            candidate = self._attach_peer_ip(candidate, scope_only=True)
            ip = (candidate.get("ip") or "").strip()
        if not ip:
            return None
        if peer_in_scope(ip, scope):
            return candidate
        return None

    def _peer_allowed(self, peer):
        """True when peer may be stored or refreshed under the active LAN scope."""
        return self._sanitize_peer_scope(peer) is not None

    def purge_out_of_scope(self, scope_ip):
        """Remove discovery entries outside the active LAN /24 scope."""
        scope = (scope_ip or "").strip()
        if not scope:
            return 0
        removed = 0
        for key in list(self.peers.keys()):
            peer = self.peers.get(key) or {}
            if (peer.get("via") or "").strip() == "serial":
                continue
            ip = (peer.get("ip") or "").strip()
            if ip and not same_lan_scope(ip, scope):
                unregister_udp_peer_ip(ip)
                del self.peers[key]
                removed += 1
        return removed

    def _peer_viable(self, peer, scope_ip=None):
        """True when a peer record can reach the local host on its transport."""
        via = (peer.get("via") or "").strip()
        if via == "serial":
            return serial_discovery_active()
        ip = (peer.get("ip") or "").strip()
        if not ip:
            return False
        if scope_ip is not None:
            scope = (scope_ip or "").strip() or None
            if scope and not peer_in_scope(ip, scope):
                return False
        return True

    def _prefer_peer(self, a, b, scope_ip=None):
        """Pick the better peer when two records share identity — fastest viable path wins."""
        a_ok = self._peer_viable(a, scope_ip)
        b_ok = self._peer_viable(b, scope_ip)
        if a_ok != b_ok:
            return a if a_ok else b
        a_rtt = a.get("rtt_avg_ms")
        b_rtt = b.get("rtt_avg_ms")
        if a_rtt is not None and b_rtt is not None and a_rtt != b_rtt:
            return a if a_rtt < b_rtt else b
        if a_rtt is not None and b_rtt is None:
            return a
        if b_rtt is not None and a_rtt is None:
            return b
        rank_a = self._peer_rank(a)
        rank_b = self._peer_rank(b)
        if rank_a != rank_b:
            return a if rank_a > rank_b else b
        return a if a.get("last_seen", 0) >= b.get("last_seen", 0) else b

    def dedupe_identities(self, scope_ip=None):
        """Collapse duplicate rows per identity, keeping the fastest viable transport."""
        scope = (scope_ip or self._scope_ip() or "").strip() or None
        grouped = {}
        for key, peer in list(self.peers.items()):
            grouped.setdefault(self._peer_dedup_key(peer), []).append((key, peer))
        removed = 0
        for entries in grouped.values():
            if len(entries) < 2:
                continue
            winner_key, winner = entries[0]
            for key, peer in entries[1:]:
                preferred = self._prefer_peer(winner, peer, scope)
                if preferred is peer:
                    self._remove_peer_entry(winner_key)
                    winner_key, winner = key, peer
                    removed += 1
                else:
                    self._remove_peer_entry(key)
                    removed += 1
            if not self._peer_viable(winner, scope):
                self._remove_peer_entry(winner_key)
                removed += 1
        return removed

    def refresh_paths_for_scope(self, scope_ip=None):
        """After LAN scope changes, drop stale LAN dupes and keep fastest viable path."""
        scope = (scope_ip or self._scope_ip() or "").strip() or None
        removed = 0
        if scope:
            removed += self.purge_out_of_scope(scope)
        removed += self.purge_misclassified_serial()
        removed += self.dedupe_identities(scope)
        return removed

    def reset_peer_probe_state(self, hash_hex):
        """Announce/beacon refresh — peer is alive; do not probe-evict."""
        clean = normalize_hash(hash_hex)
        if not clean or clean not in self.peers:
            return
        peer = self.peers[clean]
        peer["probe_failures"] = 0
        peer["last_probe_ok"] = float(peer.get("last_seen") or time.time())

    def update_peer_probe(self, hash_hex, rtt_ms=None, ok=True):
        """Record optional probe RTT; never overrides fresh announce liveness."""
        clean = normalize_hash(hash_hex)
        if not clean or clean not in self.peers:
            return
        peer = self.peers[clean]
        now = time.time()
        last_seen = float(peer.get("last_seen") or 0)
        if last_seen and (now - last_seen) < 12:
            peer["probe_failures"] = 0
            peer["last_probe_ok"] = last_seen
        if ok and rtt_ms is not None:
            from chatxz.core.peer_probe import rolling_avg_ms, avg_ms
            samples = rolling_avg_ms(peer.get("rtt_samples"), rtt_ms)
            peer["rtt_samples"] = samples
            peer["rtt_ms"] = int(rtt_ms)
            peer["rtt_avg_ms"] = avg_ms(samples)
            peer["last_probe_ok"] = now
            peer["probe_failures"] = 0
        elif not ok:
            if last_seen and (now - last_seen) < 12:
                return
            peer["probe_failures"] = int(peer.get("probe_failures") or 0) + 1
            peer["last_probe_at"] = now

    def purge_stale_probes(self, max_rtt_ms=10000, stale_s=30, max_failures=5):
        """Drop LAN peers only after stale announces AND repeated probe failures."""
        now = time.time()
        removed = []
        for key in list(self.peers.keys()):
            peer = self.peers.get(key) or {}
            via = (peer.get("via") or "").strip()
            if via == "serial":
                continue
            last_seen = float(peer.get("last_seen") or 0)
            announce_age = now - last_seen
            if announce_age <= stale_s:
                continue
            last_probe_ok = float(peer.get("last_probe_ok") or 0)
            probe_age = now - last_probe_ok if last_probe_ok else announce_age
            rtt_avg = peer.get("rtt_avg_ms")
            samples = peer.get("rtt_samples") or []
            failures = int(peer.get("probe_failures") or 0)
            if (
                rtt_avg is not None
                and len(samples) >= 3
                and int(rtt_avg) > max_rtt_ms
                and probe_age > stale_s
            ):
                removed.append(key)
                continue
            if announce_age > stale_s and probe_age > stale_s and failures >= max_failures:
                removed.append(key)
        for key in removed:
            self._remove_peer_entry(key)
        return len(removed)

    def purge_ipless_non_serial(self):
        """Drop LAN/beacon peers with no in-scope IP (ghost entries when USB is up)."""
        removed = 0
        for key in list(self.peers.keys()):
            peer = self.peers.get(key) or {}
            if (peer.get("via") or "").strip() == "serial":
                continue
            if (peer.get("ip") or "").strip():
                continue
            del self.peers[key]
            removed += 1
        return removed

    def purge_misclassified_serial(self):
        """Drop serial-tagged peers whose latest announce did not arrive on USB."""
        removed = 0
        for key in list(self.peers.keys()):
            peer = self.peers.get(key) or {}
            if (peer.get("via") or "").strip() != "serial":
                continue
            hash_hex = normalize_hash(peer.get("hash") or key)
            try:
                dest = bytes.fromhex(hash_hex)
            except ValueError:
                dest = None
            pkt_iface = announce_packet_receiving_interface(dest) if dest else None
            pkt_fam = interface_family(pkt_iface) if pkt_iface else ""
            if pkt_fam == "serial" or not pkt_iface:
                continue
            del self.peers[key]
            self._last_log.pop(hash_hex, None)
            removed += 1
        return removed

    def purge_offline_serial_peers(self):
        """Remove via=serial discovery entries when local USB serial is unplugged."""
        if serial_discovery_active():
            return 0
        removed = 0
        for key in list(self.peers.keys()):
            peer = self.peers.get(key) or {}
            if (peer.get("via") or "").strip() != "serial":
                continue
            hash_hex = normalize_hash(peer.get("hash") or key)
            del self.peers[key]
            self._last_log.pop(hash_hex, None)
            removed += 1
        return removed

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

    def _attach_peer_ip(self, peer, scope_only=False):
        if (peer.get("via") or "").strip() == "serial":
            peer = dict(peer)
            peer.pop("ip", None)
            return peer
        if (peer.get("ip") or "").strip():
            return peer
        scope = self._scope_ip() if scope_only else None
        name = (peer.get("name") or "").strip()
        ident = normalize_hash(peer.get("identity_hash"))
        for existing in self.peers.values():
            existing_ip = (existing.get("ip") or "").strip()
            if not existing_ip:
                continue
            if scope and not peer_in_scope(existing_ip, scope):
                continue
            if name and existing.get("name") == name:
                if (existing.get("via") or "").strip() == "serial":
                    return peer
                peer["ip"] = existing_ip
                peer["port"] = existing.get("port", 8742)
                return peer
            if ident and normalize_hash(existing.get("identity_hash")) == ident:
                if (existing.get("via") or "").strip() == "serial":
                    return peer
                peer["ip"] = existing_ip
                peer["port"] = existing.get("port", 8742)
                return peer
        return peer

    def _evict_superseded_peers(self, peer):
        """Drop older entries for the same host when identity/hash changes."""
        peer = dict(peer)
        new_via = (peer.get("via") or "").strip()
        if new_via != "serial":
            peer = self._attach_peer_ip(peer, scope_only=bool(self._scope_ip()))
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
            existing_via = (existing.get("via") or "").strip()
            same_name = (
                name
                and existing_name
                and name == existing_name
                and name != new_hash[:8]
                and existing_name != normalize_hash(existing.get("hash"))[:8]
            )
            if same_hash:
                continue
            if same_ident or same_pubkey:
                removed.append(normalize_hash(existing.get("hash")) or key)
                del self.peers[key]
                continue
            if (
                same_name
                and new_via == "serial"
                and existing_via in ("rns", "beacon")
            ):
                removed.append(normalize_hash(existing.get("hash")) or key)
                del self.peers[key]
                continue
            if serial_discovery_active() and same_name and not same_host:
                continue
            if same_name and {"serial", "rns"} & {new_via, existing_via}:
                if new_ident and normalize_hash(existing.get("identity_hash")) == new_ident:
                    continue
                if new_pubkey and existing.get("pubkey") == new_pubkey:
                    continue
            if same_host or same_name:
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

    def _remove_peer_entry(self, hash_hex):
        """Drop a peer and notify listeners (e.g. scope change or subnet move)."""
        clean = normalize_hash(hash_hex)
        if not clean or clean not in self.peers:
            return
        removed_ip = (self.peers.get(clean) or {}).get("ip")
        if removed_ip:
            unregister_udp_peer_ip(removed_ip)
        del self.peers[clean]
        self._last_log.pop(clean, None)
        self._last_log.pop(f"beacon:{clean}", None)
        self._last_log.pop(f"beacon-id:{clean}", None)
        self._notify_peer_evicted([clean], None)

    def register_link_peer(self, peer_hash, name="", via="link", ip=None):
        """Register a peer learned from an established RNS link (e.g. TCP hub)."""
        hash_hex = normalize_hash(peer_hash)
        if not hash_hex:
            return
        record = {
            "hash": hash_hex,
            "name": (name or hash_hex[:8]).strip() or hash_hex[:8],
            "via": via,
            "last_seen": time.time(),
        }
        if ip:
            record["ip"] = (ip or "").strip()
        if not self._peer_allowed(record):
            return
        self._store_peer(record)

    def _store_peer(self, peer):
        hash_hex = normalize_hash(peer.get("hash"))
        if not hash_hex:
            return False
        peer["hash"] = hash_hex
        sanitized = self._sanitize_peer_scope(peer)
        if not sanitized:
            self._remove_peer_entry(hash_hex)
            return False
        peer = sanitized
        if serial_discovery_active():
            from chatxz.core.lan_rns import peer_path_on_family
            if peer_path_on_family(hash_hex, "serial"):
                peer["via"] = "serial"
                peer.pop("ip", None)
        elif (peer.get("via") or "").strip() == "serial":
            peer.pop("ip", None)
        removed, peer = self._evict_superseded_peers(peer)
        if removed:
            self._notify_peer_evicted(removed, peer)
        existing = self.peers.get(hash_hex)
        if existing:
            existing_via = (existing.get("via") or "").strip()
            new_via = (peer.get("via") or "").strip()
            if new_via == "serial" and serial_discovery_active():
                existing["via"] = "serial"
                existing.pop("ip", None)
            elif existing_via == "serial" and new_via in ("rns", "beacon"):
                if not serial_discovery_active():
                    existing["via"] = new_via
            new_ip = (peer.get("ip") or "").strip()
            scope = self._scope_ip()
            if scope and existing.get("ip") and not peer_in_scope(existing.get("ip"), scope):
                if serial_discovery_active():
                    existing.pop("ip", None)
                elif not new_ip or peer_in_scope(new_ip, scope):
                    existing.pop("ip", None)
            if new_ip and new_ip != (existing.get("ip") or "").strip():
                if not scope or peer_in_scope(new_ip, scope):
                    existing["ip"] = new_ip
                    existing["port"] = peer.get("port", existing.get("port", 8742))
                elif serial_discovery_active():
                    existing.pop("ip", None)
                elif scope:
                    self._remove_peer_entry(hash_hex)
                    return False
            elif peer.get("ip") and not existing.get("ip"):
                existing_via = (existing.get("via") or "").strip()
                if existing_via != "serial" or not serial_discovery_active():
                    if not scope or peer_in_scope(peer["ip"], scope):
                        existing["ip"] = peer["ip"]
                        existing["port"] = peer.get("port", 8742)
            if (existing.get("via") or "").strip() == "serial" and serial_discovery_active():
                existing.pop("ip", None)
            if peer.get("identity_hash") and not existing.get("identity_hash"):
                existing["identity_hash"] = peer["identity_hash"]
            if peer.get("pubkey") and not existing.get("pubkey"):
                existing["pubkey"] = peer["pubkey"]
            if peer.get("name") and peer["name"] != hash_hex[:8]:
                existing["name"] = peer["name"]
            existing_via = (existing.get("via") or "").strip()
            new_via = (peer.get("via") or "").strip()
            if new_via == "serial" and serial_discovery_active():
                existing["via"] = "serial"
                existing.pop("ip", None)
            elif peer.get("via") != "beacon" or existing.get("via") == "beacon":
                existing["via"] = new_via or existing_via
            existing["last_seen"] = peer.get("last_seen", time.time())
            peer = existing
        else:
            self.peers[hash_hex] = peer
        if peer.get("ip"):
            register_udp_peer_ip(peer["ip"])
        self.reset_peer_probe_state(hash_hex)
        if self.on_peer_seen:
            try:
                self.on_peer_seen(peer)
            except Exception as e:
                print(f"[discovery] on_peer_seen error: {e}")
        return True

    def _on_announce(self, destination_hash, app_data, announced_identity=None):
        if not self.running or not self.accept_peers:
            return

        hash_hex = normalize_hash(RNS.hexrep(destination_hash))
        identity_hex = ""
        if announced_identity and hasattr(announced_identity, "hash"):
            identity_hex = normalize_hash(RNS.hexrep(announced_identity.hash))
        name = ""
        app_name = ""

        announce_ip = ""
        if app_data:
            try:
                data = json.loads(app_data.decode("utf-8"))
                app_name = data.get("app", "")
                name = data.get("name", "")
                announce_ip = (data.get("ip") or "").strip()
            except Exception:
                pass

        if app_name != APP_NAME:
            return

        packet_iface = announce_packet_receiving_interface(destination_hash)
        packet_fam = interface_family(packet_iface) if packet_iface else ""
        scope = self._scope_ip()
        via = "rns"
        if packet_fam == "serial":
            if announce_ip or not serial_discovery_active():
                return
            via = "serial"
            announce_ip = ""
        elif packet_fam in ("udp", "lan", "tcp"):
            if not announce_ip:
                return
            if scope and not peer_in_scope(announce_ip, scope):
                return
            via = "rns"
        elif announce_ip:
            if scope and not peer_in_scope(announce_ip, scope):
                return
            via = "rns"
        elif serial_discovery_active():
            via = "serial"
        else:
            return
        if via == "serial":
            announce_ip = ""
        peer = {
            "hash": hash_hex,
            "name": name or hash_hex[:8],
            "app": app_name,
            "last_seen": time.time(),
            "via": via,
        }
        if announce_ip:
            peer["ip"] = announce_ip
        if identity_hex:
            peer["identity_hash"] = identity_hex
        if announced_identity:
            try:
                peer["pubkey"] = base64.b64encode(
                    announced_identity.get_public_key()
                ).decode("ascii")
            except Exception:
                pass
        if not self._store_peer(peer):
            return
        try:
            from chatxz.core.peer_identity import register_identity_from_announce
            register_identity_from_announce(peer, announced_identity)
        except Exception:
            pass
        if (peer.get("via") or "").strip() == "serial":
            try:
                from chatxz.core.lan_rns import reinforce_serial_peer_path
                reinforce_serial_peer_path(hash_hex)
            except Exception:
                pass
        via = peer.get("via", "rns")
        label = name or hash_hex[:12]
        ip_hint = (self.peers.get(hash_hex) or {}).get("ip")
        if ip_hint:
            label = f"{label} ({ip_hint})"
        elif via == "serial":
            label = f"{label} (serial)"
        self._log_once(
            hash_hex,
            f"[discovery] RNS peer discovered ({via}): {label}...",
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
            from chatxz.utils.platform import discovery_scope_ip
            local_ip = (discovery_scope_ip() or "").strip()
        except Exception:
            local_ip = ""
        if local_ip:
            if source and not peer_in_scope(source, local_ip):
                return False
            if peer_ip and not peer_in_scope(peer_ip, local_ip):
                return False
            effective_ip = peer_ip or source
            if not effective_ip or not peer_in_scope(effective_ip, local_ip):
                return False
            peer_ip = effective_ip
        else:
            peer_ip = peer_ip or source or peer.get("ip")
        peer["ip"] = peer_ip
        if not self._peer_allowed(peer):
            return False
        peer["port"] = data.get("port", peer.get("port", 8742))
        name = peer.get("name") or hash_hex[:8]
        peer["name"] = name
        existing = self.peers.get(hash_hex) or {}
        existing_via = (existing.get("via") or "").strip()
        if existing_via == "serial" and serial_discovery_active():
            existing["last_seen"] = time.time()
            if register_identity_from_beacon(data):
                self._log_once(
                    f"beacon-id:{hash_hex}",
                    f"[discovery] Beacon identity refreshed: {name} (serial-only)",
                )
            self.peers[hash_hex] = existing
            return True
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
        if peer.get("via") == "serial":
            score += 22
        elif peer.get("via") in ("rns", "beacon"):
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

    def _same_ip_preferred(self, a, b, scope_ip=None):
        """Pick the better peer record when two entries share an IP."""
        a_verified = bool(a.get("pubkey"))
        b_verified = bool(b.get("pubkey"))
        if a_verified != b_verified:
            return a if a_verified else b
        if a.get("last_seen", 0) != b.get("last_seen", 0):
            return a if a.get("last_seen", 0) >= b.get("last_seen", 0) else b
        a_via = (a.get("via") or "").strip()
        b_via = (b.get("via") or "").strip()
        if a_via == "rns" and b_via == "beacon":
            return a
        if b_via == "rns" and a_via == "beacon":
            return b
        return self._prefer_peer(a, b, scope_ip)

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
                    deduped[key] = self._prefer_peer(existing, peer, scope_ip)
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
            deduped[key] = self._prefer_peer(existing, peer, scope_ip)
        collapsed = {}
        no_ip_peers = []
        usb_up = serial_discovery_active()
        for peer in deduped.values():
            if (peer.get("via") or "").strip() == "serial":
                if not usb_up:
                    continue
                serial_peer = dict(peer)
                serial_peer.pop("ip", None)
                no_ip_peers.append(serial_peer)
                continue
            ip = (peer.get("ip") or "").strip()
            if not ip:
                if (peer.get("via") or "").strip() == "serial":
                    no_ip_peers.append(peer)
                continue
            existing = collapsed.get(ip)
            if not existing:
                collapsed[ip] = peer
                continue
            collapsed[ip] = self._same_ip_preferred(peer, existing, scope_ip)
        merged = no_ip_peers + list(collapsed.values())
        return [p for p in merged if self._peer_viable(p, scope_ip)]

    def current_hashes(self):
        hashes = set()
        for peer in self.get_peers():
            for key in ("hash", "identity_hash"):
                clean = normalize_hash(peer.get(key))
                if clean:
                    hashes.add(clean)
        return hashes

    def peer_is_current(self, peer_hash, scope_ip=None):
        clean = normalize_hash(peer_hash)
        if not clean:
            return False
        scope = scope_ip if scope_ip is not None else self._scope_ip()
        hashes = set()
        for peer in self.get_peers(scope_ip=scope):
            for key in ("hash", "identity_hash"):
                entry = normalize_hash(peer.get(key))
                if entry:
                    hashes.add(entry)
        return clean in hashes
