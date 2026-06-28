"""Lightweight peer liveness probes (UDP beacon ping + RNS link ping)."""

import json
import socket
import threading
import time
import uuid

from chatxz.core.lan_beacon import BEACON_PORT, MAGIC
from chatxz.core.lan_rns import peer_path_on_family, request_paths_for_hash

PROBE_INTERVAL_S = 30
PROBE_INTERVAL_MIN_S = 0
PROBE_INTERVAL_SERIAL_MIN_S = 3
PROBE_INTERVAL_MAX_S = 18000
ANNOUNCE_INTERVAL_MAX_S = 18000
PROBE_TIMEOUT_S = 3.0
PROBE_MAX_RTT_MS = 10000
PROBE_STALE_S = 30
PROBE_AVG_WINDOW = 6
PROBE_PACKET_MIN_BYTES = 32
PROBE_PACKET_MAX_BYTES = 1472
PROBE_PACKET_DEFAULT_BYTES = 64


def clamp_probe_interval(seconds):
    """User-configurable ping interval for LAN liveness checks (0 = off)."""
    try:
        value = int(seconds)
    except (TypeError, ValueError):
        value = PROBE_INTERVAL_S
    return max(PROBE_INTERVAL_MIN_S, min(PROBE_INTERVAL_MAX_S, value))


def clamp_serial_probe_interval(seconds):
    """Serial probe interval — minimum 3s between USB path checks (0 = off)."""
    try:
        value = int(seconds)
    except (TypeError, ValueError):
        value = PROBE_INTERVAL_S
    if value <= 0:
        return 0
    return max(PROBE_INTERVAL_SERIAL_MIN_S, min(PROBE_INTERVAL_MAX_S, value))


def probe_packet_bytes():
    """Fixed minimum UDP probe size for LAN RTT (not user-adjustable)."""
    return PROBE_PACKET_MIN_BYTES


def clamp_announce_interval(seconds):
    """Per-transport auto-announce interval (0 = off, max 5 hours)."""
    try:
        value = int(seconds)
    except (TypeError, ValueError):
        return 0
    return max(0, min(ANNOUNCE_INTERVAL_MAX_S, value))


def link_rtt_ms(messaging, hash_hex):
    """RTT from an active RNS link to the peer, if any."""
    if not messaging or not hash_hex:
        return None
    clean = (hash_hex or "").replace(":", "").strip().lower()
    if len(clean) != 32:
        return None
    try:
        link = messaging._link_for_peer(clean)
    except Exception:
        return None
    if not link:
        return None
    try:
        import RNS
        if link.status != RNS.Link.ACTIVE:
            return None
    except Exception:
        pass
    rtt = getattr(link, "rtt", None)
    if rtt is None:
        return None
    try:
        return max(1, int(float(rtt) * 1000))
    except (TypeError, ValueError):
        return None

_pending_probes = {}
_pending_lock = threading.Lock()


def register_probe_ack(probe_id, rtt_ms, source_ip=""):
    with _pending_lock:
        entry = _pending_probes.pop(probe_id, None)
    if entry is None:
        return False
    entry["rtt_ms"] = int(rtt_ms)
    entry["source_ip"] = source_ip
    entry["event"].set()
    return True


def _send_udp_probe(sock, host, probe_id, ts, packet_bytes=None):
    if packet_bytes is None:
        packet_bytes = probe_packet_bytes()
    payload = {
        "app": "chatxz",
        "type": "probe",
        "id": probe_id,
        "ts": ts,
    }
    packet = MAGIC + json.dumps(payload).encode("utf-8")
    target = max(PROBE_PACKET_MIN_BYTES, int(packet_bytes or PROBE_PACKET_MIN_BYTES))
    if len(packet) < target:
        packet += b"\x00" * (target - len(packet))
    sock.sendto(packet, (host, BEACON_PORT))


def probe_udp_peer(host, timeout_s=PROBE_TIMEOUT_S, packet_bytes=None):
    if packet_bytes is None:
        packet_bytes = probe_packet_bytes()
    host = (host or "").strip()
    if not host:
        return None
    probe_id = uuid.uuid4().hex[:12]
    event = threading.Event()
    ts = time.time()
    with _pending_lock:
        _pending_probes[probe_id] = {"event": event, "rtt_ms": None, "ts": ts}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(0.5)
        _send_udp_probe(sock, host, probe_id, ts, packet_bytes=packet_bytes)
        if not event.wait(timeout_s):
            with _pending_lock:
                _pending_probes.pop(probe_id, None)
            return None
        with _pending_lock:
            entry = _pending_probes.pop(probe_id, None)
        if entry and entry.get("rtt_ms") is not None:
            return int(entry["rtt_ms"])
    except OSError:
        pass
    finally:
        sock.close()
    return None


def probe_serial_path(hash_hex, timeout_s=PROBE_TIMEOUT_S):
    clean = (hash_hex or "").replace(":", "").strip().lower()
    if len(clean) != 32:
        return None
    start = time.time()
    try:
        request_paths_for_hash(clean, family="serial")
    except Exception:
        return None
    deadline = start + timeout_s
    while time.time() < deadline:
        if peer_path_on_family(clean, "serial"):
            return int((time.time() - start) * 1000)
        time.sleep(0.08)
    return None


def rolling_avg_ms(samples, value):
    if value is None:
        return samples
    samples = list(samples or [])
    samples.append(int(value))
    return samples[-PROBE_AVG_WINDOW:]


def avg_ms(samples):
    vals = [int(v) for v in (samples or []) if v is not None]
    if not vals:
        return None
    return int(sum(vals) / len(vals))