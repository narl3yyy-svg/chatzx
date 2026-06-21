import json
import time
import RNS

DISCOVERY_TIMEOUT = 45
APP_NAME = "chatxz"

class PeerDiscovery:
    def __init__(self):
        self.peers = {}
        self.running = False
        self._handler = None

    def start(self):
        self.running = True
        self._handler = self._announce_handler
        RNS.Transport.register_announce_handler(self._handler)
        print("[discovery] Registered RNS announce handler")

    def stop(self):
        self.running = False

    def _announce_handler(self, destination_hash, identity, app_data):
        if not self.running:
            return
        try:
            if not app_data:
                return

            try:
                data = json.loads(app_data.decode("utf-8"))
            except:
                return

            app_name = data.get("app", "")
            if app_name != APP_NAME:
                return

            hash_hex = RNS.hexrep(destination_hash)
            name = data.get("name", "") or hash_hex[:8]

            self.peers[hash_hex] = {
                "hash": hash_hex,
                "name": name,
                "last_seen": time.time(),
            }
            print(f"[discovery] Peer seen: {hash_hex[:12]}... ({name})")
        except:
            pass

    def get_peers(self):
        now = time.time()
        stale = [h for h, p in self.peers.items() if now - p["last_seen"] > DISCOVERY_TIMEOUT]
        for h in stale:
            del self.peers[h]
        return list(self.peers.values())
