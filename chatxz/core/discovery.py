import os
import json
import socket
import struct
import time
import threading

DISCOVERY_PORT = 8743
DISCOVERY_INTERVAL = 4
DISCOVERY_TIMEOUT = 15

class PeerDiscovery:
    def __init__(self, identity_hash, display_name="", port=DISCOVERY_PORT):
        self.identity_hash = identity_hash
        self.display_name = display_name
        self.port = port
        self.peers = {}
        self.running = False
        self._broadcast_thread = None
        self._listen_thread = None
        self._sock = None
        self._broadcast_sock = None
        self._beacon = None

    def make_beacon(self):
        data = {
            "type": "chatzx_peer",
            "version": 1,
            "hash": self.identity_hash,
            "name": self.display_name,
            "port": 8742,
        }
        return json.dumps(data).encode("utf-8")

    def start(self):
        self._beacon = self.make_beacon()
        self.running = True

        # Listen socket: receive beacons from anyone
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("", self.port))
        self._sock.settimeout(0.5)

        # Broadcast socket: send beacons to LAN
        self._broadcast_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._broadcast_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._broadcast_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._broadcast_sock.bind(("", 0))
        self._broadcast_sock.settimeout(0.5)

        self._broadcast_thread = threading.Thread(target=self._broadcast_loop, daemon=True)
        self._listen_thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._broadcast_thread.start()
        self._listen_thread.start()

        print(f"[discovery] Started on UDP {self.port}")

    def stop(self):
        self.running = False
        for s in (self._sock, self._broadcast_sock):
            if s:
                try:
                    s.close()
                except:
                    pass
        print(f"[discovery] Stopped")

    def send_beacon(self):
        """Send one immediate beacon to broadcast address."""
        try:
            self._broadcast_sock.sendto(self._beacon, ("255.255.255.255", self.port))
            # Also try subnet-directed broadcasts for each interface
            ifaces = self._get_broadcast_addrs()
            for bcast in ifaces:
                try:
                    self._broadcast_sock.sendto(self._beacon, (bcast, self.port))
                except:
                    pass
            return True
        except Exception as e:
            print(f"[discovery] Send error: {e}")
            return False

    def _get_broadcast_addrs(self):
        """Discover broadcast addresses for all network interfaces."""
        addrs = []
        try:
            import subprocess
            result = subprocess.run(
                ["ip", "-4", "addr", "show", "scope", "global"],
                capture_output=True, text=True, timeout=3
            )
            for line in result.stdout.splitlines():
                if "brd" in line:
                    parts = line.split()
                    for i, p in enumerate(parts):
                        if p == "brd" and i + 1 < len(parts):
                            addrs.append(parts[i + 1])
        except:
            pass
        if not addrs:
            addrs.append("255.255.255.255")
        return addrs

    def _broadcast_loop(self):
        while self.running:
            self.send_beacon()
            for _ in range(DISCOVERY_INTERVAL):
                if not self.running:
                    return
                time.sleep(1)

    def _listen_loop(self):
        while self.running:
            try:
                data, addr = self._sock.recvfrom(4096)
                try:
                    msg = json.loads(data.decode("utf-8"))
                    if msg.get("type") == "chatzx_peer" and msg.get("hash") != self.identity_hash:
                        peer_hash = msg["hash"]
                        peer_name = msg.get("name", "") or peer_hash[:8]
                        peer_addr = addr[0]
                        peer_port = msg.get("port", 8742)
                        self.peers[peer_hash] = {
                            "hash": peer_hash,
                            "name": peer_name,
                            "address": peer_addr,
                            "port": peer_port,
                            "last_seen": time.time(),
                        }
                except:
                    pass
            except socket.timeout:
                continue
            except:
                if self.running:
                    time.sleep(0.1)

    def get_peers(self):
        now = time.time()
        stale = [h for h, p in self.peers.items() if now - p["last_seen"] > DISCOVERY_TIMEOUT]
        for h in stale:
            del self.peers[h]
        return list(self.peers.values())
