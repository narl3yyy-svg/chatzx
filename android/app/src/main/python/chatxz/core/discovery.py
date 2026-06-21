import json
import time
import RNS

DISCOVERY_TIMEOUT = 60
APP_NAME = "chatxz"

class AnnounceHandler:
    aspect_filter = None

    def __init__(self, discovery):
        self.discovery = discovery

    def received_announce(self, destination_hash, announced_identity, app_data):
        self.discovery._on_announce(destination_hash, app_data)


class PeerDiscovery:
    def __init__(self):
        self.peers = {}
        self.running = False
        self._handler = None

    def start(self):
        self.running = True
        self._handler = AnnounceHandler(self)
        RNS.Transport.register_announce_handler(self._handler)
        print("[discovery] Announce handler registered")

    def stop(self):
        self.running = False

    def _on_announce(self, destination_hash, app_data):
        if not self.running:
            return

        hash_hex = RNS.hexrep(destination_hash)
        name = ""
        app_name = ""

        if app_data:
            try:
                data = json.loads(app_data.decode("utf-8"))
                app_name = data.get("app", "")
                name = data.get("name", "")
            except:
                pass

        if app_name != APP_NAME:
            return

        self.peers[hash_hex] = {
            "hash": hash_hex,
            "name": name or hash_hex[:8],
            "app": app_name,
            "last_seen": time.time(),
        }
        print(f"[discovery] Peer discovered: {name or hash_hex[:12]}...")

    def get_peers(self):
        now = time.time()
        stale = [h for h, p in self.peers.items() if now - p["last_seen"] > DISCOVERY_TIMEOUT]
        for h in stale:
            del self.peers[h]
        return list(self.peers.values())
