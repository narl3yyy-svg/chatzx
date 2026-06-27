"""UDP LAN beacon for peer discovery (supplements RNS announces)."""

import base64
import json
import socket
import threading
import time

from chatxz.core.discovery import APP_NAME
from chatxz.core.lan_rns import register_udp_peer_ip
from chatxz.core.lan_targets import directed_broadcasts, efficient_unicast_hosts
from chatxz.utils.platform import is_android, lan_ip

BEACON_PORT = 8743
MAGIC = b"CHATXZ1"


class LanBeacon:
    def __init__(self, discovery, dest_hash, display_name="", ip=None, port=8742,
                 periodic=False, identity_hash=None, identity_pubkey=None,
                 on_periodic=None):
        self.discovery = discovery
        self.dest_hash = (dest_hash or "").replace(":", "")
        self.identity_hash = (identity_hash or "").replace(":", "")
        self.identity_pubkey = identity_pubkey
        self.on_periodic = on_periodic
        self.display_name = display_name or ""
        self.ip = ip
        self.port = port
        self.periodic = periodic
        self.running = False
        self._rx_sock = None
        self._tx_sock = None
        self._listen_thread = None
        self._periodic_thread = None
        self.last_send_targets = []
        self.last_subnet_probes = 0
        self.last_broadcast_sent = 0
        self.last_announce_sent = 0
        self.packets_sent = 0
        self.packets_received = 0
        self._interval = 30

    def start(self):
        if self.running:
            return
        self.running = True
        self._rx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._rx_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._rx_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        self._rx_sock.bind(("0.0.0.0", BEACON_PORT))
        self._rx_sock.settimeout(1.0)

        self._tx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._tx_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            self._tx_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except OSError:
            pass

        self._listen_thread = threading.Thread(target=self._listen, name="chatxz-beacon-rx", daemon=True)
        self._listen_thread.start()
        if self.periodic:
            self._periodic_thread = threading.Thread(target=self._periodic, name="chatxz-beacon-tx", daemon=True)
            self._periodic_thread.start()
        else:
            self._periodic_thread = None
        print(f"[beacon] Listening on UDP {BEACON_PORT} (periodic={'on' if self.periodic else 'off'})")

    def set_interval(self, seconds):
        """Beacon announce interval (seconds)."""
        try:
            value = int(seconds)
        except (TypeError, ValueError):
            value = self._interval
        self._interval = max(5, min(300, value))

    def set_periodic(self, enabled, on_periodic=None):
        """Enable or disable background beacon announces without restarting."""
        enabled = bool(enabled)
        if on_periodic is not None:
            self.on_periodic = on_periodic
        if enabled == self.periodic:
            return
        self.periodic = enabled
        if enabled and self.running:
            if self._periodic_thread and self._periodic_thread.is_alive():
                return
            self._periodic_thread = threading.Thread(
                target=self._periodic, name="chatxz-beacon-tx", daemon=True,
            )
            self._periodic_thread.start()
            print(f"[beacon] Periodic announces enabled (every {self._interval}s)")

    def stop(self):
        self.running = False
        for sock in (self._rx_sock, self._tx_sock):
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass
        self._rx_sock = None
        self._tx_sock = None

    def _refresh_ip(self):
        current = lan_ip()
        if current:
            self.ip = current

    def _payload(self):
        self._refresh_ip()
        payload = {
            "app": APP_NAME,
            "v": 1,
            "hash": self.dest_hash,
            "name": self.display_name,
            "ip": self.ip or "",
            "port": self.port,
        }
        if self.identity_hash:
            payload["identity_hash"] = self.identity_hash
        if self.identity_pubkey:
            try:
                payload["pubkey"] = base64.b64encode(self.identity_pubkey).decode("ascii")
            except Exception:
                pass
        return json.dumps(payload).encode("utf-8")

    def _known_peer_ips(self):
        from chatxz.core.lan_rns import known_udp_peer_ips
        return known_udp_peer_ips()

    def _unicast_targets(self, subnet_probe=False):
        if not subnet_probe:
            return []
        return efficient_unicast_hosts(
            ip=self.ip or lan_ip(),
            known_ips=self._known_peer_ips(),
        )

    def send(self, count=3, subnet_probe=False):
        if not self._tx_sock or not self.running:
            return 0
        packet = MAGIC + self._payload()
        sent = 0
        broadcasts = 0
        android = is_android()
        if subnet_probe is False and android:
            subnet_probe = True

        if subnet_probe:
            probed = 0
            for host in self._unicast_targets(subnet_probe=True):
                try:
                    self._tx_sock.sendto(packet, (host, BEACON_PORT))
                    sent += 1
                    probed += 1
                except OSError:
                    pass
            self.last_subnet_probes = probed
            if probed:
                print(f"[beacon] Unicast probe sent to {probed} host(s)")

        targets = directed_broadcasts(self.ip or lan_ip())
        self.last_send_targets = targets
        for _ in range(count):
            for addr in targets:
                try:
                    self._tx_sock.sendto(packet, (addr, BEACON_PORT))
                    sent += 1
                    broadcasts += 1
                except OSError as exc:
                    if not android:
                        print(f"[beacon] broadcast to {addr}:{BEACON_PORT} failed: {exc}")

        self.last_broadcast_sent = broadcasts
        self.last_announce_sent = sent
        self.packets_sent += sent
        if sent:
            print(
                f"[beacon] Sent {sent} packet(s) "
                f"(broadcast={broadcasts}, unicast={self.last_subnet_probes}); "
                f"targets={self.last_send_targets}"
            )
        return sent

    def _listen(self):
        while self.running and self._rx_sock:
            try:
                data, addr = self._rx_sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if len(data) < len(MAGIC) + 2 or not data.startswith(MAGIC):
                continue
            try:
                payload = json.loads(data[len(MAGIC):].decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            msg_type = payload.get("type") or ""
            if msg_type == "probe":
                ack = {
                    "app": APP_NAME,
                    "type": "probe_ack",
                    "id": payload.get("id"),
                    "ts": payload.get("ts"),
                }
                try:
                    if self._tx_sock:
                        self._tx_sock.sendto(
                            MAGIC + json.dumps(ack).encode("utf-8"),
                            (addr[0], BEACON_PORT),
                        )
                except OSError:
                    pass
                continue
            if msg_type == "probe_ack":
                from chatxz.core.peer_probe import register_probe_ack
                probe_id = payload.get("id")
                sent_ts = float(payload.get("ts") or 0)
                if probe_id and sent_ts:
                    rtt_ms = max(0, int((time.time() - sent_ts) * 1000))
                    register_probe_ack(probe_id, rtt_ms, source_ip=addr[0])
                continue
            if not payload.get("ip"):
                payload["ip"] = addr[0]
            if self.discovery._on_beacon(
                payload, self.dest_hash, self.identity_hash, source_ip=addr[0],
            ):
                self.packets_received += 1
                register_udp_peer_ip(payload.get("ip") or addr[0])

    def _periodic(self):
        time.sleep(4)
        while self.running:
            self.send(count=1, subnet_probe=is_android())
            if self.on_periodic:
                try:
                    self.on_periodic()
                except Exception as exc:
                    print(f"[beacon] periodic callback failed: {exc}")
            for _ in range(self._interval):
                if not self.running:
                    return
                time.sleep(1)

    def reset_stats(self):
        self.packets_sent = 0
        self.packets_received = 0
        self.last_announce_sent = 0
        self.last_broadcast_sent = 0
        self.last_subnet_probes = 0
        self.last_send_targets = []

    def status(self):
        return {
            "port": BEACON_PORT,
            "running": self.running,
            "periodic": self.periodic,
            "lan_ip": self.ip or lan_ip(),
            "broadcast_targets": self.last_send_targets,
            "last_subnet_probes": self.last_subnet_probes,
            "last_broadcast_sent": self.last_broadcast_sent,
            "last_announce_sent": self.last_announce_sent,
            "packets_sent": self.packets_sent,
            "packets_received": self.packets_received,
            "interval_sec": self._interval if self.periodic else 0,
        }
