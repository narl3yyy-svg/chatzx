import json
import time
import RNS

DISCOVERY_TIMEOUT = 60
APP_NAME = "chatxz"


def normalize_hash(h):
    return (h or "").replace("<", "").replace(">", "").replace(":", "").strip().lower()


class AnnounceHandler:
    aspect_filter = None

    def __init__(self, discovery):
        self.discovery = discovery

    def received_announce(self, destination_hash, announced_identity, app_data):
        self.discovery._on_announce(destination_hash, app_data)


class PeerDiscovery:
    def __init__(self, on_peer_seen=None):
        self.peers = {}
        self.running = False
        self._handler = None
        self.on_peer_seen = on_peer_seen
        self._last_log = {}

    def start(self):
        self.running = True
        self._handler = AnnounceHandler(self)
        RNS.Transport.register_announce_handler(self._handler)
        print("[discovery] Announce handler registered")

    def stop(self):
        self.running = False

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
        if self.on_peer_seen:
            try:
                self.on_peer_seen(peer)
            except Exception as e:
                print(f"[discovery] on_peer_seen error: {e}")

    def _on_announce(self, destination_hash, app_data):
        if not self.running:
            return

        hash_hex = normalize_hash(RNS.hexrep(destination_hash))
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

        self._store_peer({
            "hash": hash_hex,
            "name": name or hash_hex[:8],
            "app": app_name,
            "last_seen": time.time(),
            "via": "rns",
        })
        self._log_once(hash_hex, f"[discovery] RNS peer discovered: {name or hash_hex[:12]}...")

    def _on_beacon(self, data, my_hash):
        if not self.running:
            return
        if data.get("app") != APP_NAME:
            return
        hash_hex = normalize_hash(data.get("hash"))
        my_clean = normalize_hash(my_hash)
        if not hash_hex or hash_hex == my_clean:
            return
        name = data.get("name", "") or hash_hex[:8]
        self._store_peer({
            "hash": hash_hex,
            "name": name,
            "app": APP_NAME,
            "ip": data.get("ip"),
            "port": data.get("port", 8742),
            "last_seen": time.time(),
            "via": "beacon",
        })
        self._log_once(
            f"beacon:{hash_hex}",
            f"[discovery] Beacon peer discovered: {name} ({data.get('ip', '?')})",
        )

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
        now = time.time()
        stale = [h for h, p in self.peers.items() if now - p["last_seen"] > DISCOVERY_TIMEOUT]
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