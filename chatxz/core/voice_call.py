"""Voice call signaling and Opus audio framing over RNS links."""

import base64
import json
import time
import uuid

from chatxz.core.opus_native import OPUS_CODEC

# Typical Opus voip packets are 40–120 B — well under RNS link MTU (~1016 B app budget).
CALL_AUDIO_MAX_OPUS_BYTES = 400

CALL_INVITE = "__call_invite"
CALL_ACCEPT = "__call_accept"
CALL_REJECT = "__call_reject"
CALL_END = "__call_end"
CALL_AUDIO = "__call_audio"

CALL_TYPES = frozenset({
    CALL_INVITE,
    CALL_ACCEPT,
    CALL_REJECT,
    CALL_END,
    CALL_AUDIO,
})

STATE_IDLE = "idle"
STATE_OUTGOING = "outgoing"
STATE_INCOMING = "incoming"
STATE_ACTIVE = "active"


def new_call_id():
    return str(uuid.uuid4())[:12]


def estimate_call_audio_packet_size(
    raw_bytes: int,
    *,
    call_id: str = "00000000-000",
    seq: int = 9999,
    codec: str = OPUS_CODEC,
    msg_id: str = "abcd1234efgh",
    link_mtu: int = 1064,
):
    """Return encoded CALL_AUDIO JSON size and MTU budget for a payload."""
    data_b64 = base64.b64encode(bytes([0x7F]) * raw_bytes).decode("ascii")
    payload = {
        "call_id": call_id,
        "seq": seq,
        "codec": codec,
        "data": data_b64,
    }
    envelope = {
        "type": CALL_AUDIO,
        "content": json.dumps(payload),
        "timestamp": 1710000000.0,
        "msg_id": msg_id,
    }
    packet_len = len(json.dumps(envelope).encode("utf-8"))
    budget = max(400, int(link_mtu or 500) - 48)
    return packet_len, budget


def max_audio_bytes_for_mtu(link_mtu: int = 1064, codec: str = OPUS_CODEC) -> int:
    """Largest raw audio payload that fits the CALL_AUDIO JSON envelope."""
    budget = max(400, int(link_mtu or 500) - 48)
    lo, hi = 1, 2000
    while lo < hi:
        mid = (lo + hi + 1) // 2
        size, _ = estimate_call_audio_packet_size(mid, codec=codec, link_mtu=link_mtu)
        if size <= budget:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _raw_audio_chunk_fits(raw_len: int, codec: str, call_id: str, link_mtu: int) -> bool:
    piece = bytes([0x7F]) * raw_len
    b64 = base64.b64encode(piece).decode("ascii")
    payload = {
        "call_id": call_id,
        "seq": 9999,
        "codec": codec,
        "data": b64,
    }
    envelope = {
        "type": CALL_AUDIO,
        "content": json.dumps(payload),
        "timestamp": 1710000000.0,
        "msg_id": "abcd1234efgh",
    }
    budget = max(400, int(link_mtu or 500) - 48)
    return len(json.dumps(envelope).encode("utf-8")) <= budget


def split_call_audio_b64(
    audio_b64: str,
    codec: str = OPUS_CODEC,
    *,
    call_id: str = "00000000-000",
    seq: int = 1,
    link_mtu: int = 1064,
):
    """Split a base64 Opus blob into MTU-safe chunks if needed."""
    if not audio_b64:
        return []
    try:
        raw = base64.b64decode(audio_b64, validate=True)
    except Exception:
        return [audio_b64]
    if not raw:
        return []
    codec = codec or OPUS_CODEC
    if _raw_audio_chunk_fits(len(raw), codec, call_id, link_mtu):
        return [audio_b64]
    chunks = []
    pos = 0
    while pos < len(raw):
        remaining = len(raw) - pos
        lo, hi = 1, remaining
        best = 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if _raw_audio_chunk_fits(mid, codec, call_id, link_mtu):
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        if best < 1:
            break
        chunks.append(base64.b64encode(raw[pos:pos + best]).decode("ascii"))
        pos += best
    return chunks or [audio_b64]


def parse_call_payload(content):
    if not content:
        return {}
    try:
        data = json.loads(content)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


class VoiceCallSession:
    """Tracks one duplex call per messaging backend (signaling only)."""

    STALE_OUTGOING_SEC = 45.0
    STALE_INCOMING_SEC = 90.0
    STALE_ACTIVE_SEC = 7200.0

    def __init__(self):
        self.state = STATE_IDLE
        self.call_id = ""
        self.peer_hash = ""
        self.transport = ""
        self.started_at = 0.0
        self.state_since = 0.0
        self._audio_seq_out = 0

    def reset(self):
        self.state = STATE_IDLE
        self.call_id = ""
        self.peer_hash = ""
        self.transport = ""
        self.started_at = 0.0
        self.state_since = time.time()
        self._audio_seq_out = 0

    def is_busy(self):
        return self.state in (STATE_OUTGOING, STATE_INCOMING, STATE_ACTIVE)

    def is_stale(self):
        if self.state == STATE_IDLE:
            return False
        now = time.time()
        since = self.state_since or now
        if self.state == STATE_ACTIVE:
            return (now - (self.started_at or since)) > self.STALE_ACTIVE_SEC
        if self.state == STATE_INCOMING:
            return (now - since) > self.STALE_INCOMING_SEC
        if self.state == STATE_OUTGOING:
            return (now - since) > self.STALE_OUTGOING_SEC
        return False

    def begin_outgoing(self, peer_hash, transport="lan"):
        self.reset()
        self.state = STATE_OUTGOING
        self.call_id = new_call_id()
        self.peer_hash = (peer_hash or "").replace(":", "")
        self.transport = (transport or "lan").strip().lower() or "lan"
        self.state_since = time.time()
        return self.call_id

    def begin_incoming(self, call_id, peer_hash, transport="lan"):
        self.reset()
        self.state = STATE_INCOMING
        self.call_id = (call_id or new_call_id()).strip()
        self.peer_hash = (peer_hash or "").replace(":", "")
        self.transport = (transport or "lan").strip().lower() or "lan"
        self.state_since = time.time()

    def activate(self, call_id=None):
        if call_id and self.call_id and call_id != self.call_id:
            return False
        self.state = STATE_ACTIVE
        self.started_at = time.time()
        return True

    def end(self):
        cid = self.call_id
        peer = self.peer_hash
        self.reset()
        return cid, peer

    def next_audio_seq(self):
        self._audio_seq_out += 1
        return self._audio_seq_out