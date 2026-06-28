"""Duplex voice call signaling and audio frames over RNS links."""

import base64
import json
import time
import uuid

# μ-law bytes per frame that fit RNS link MTU (1064 B hardware → ~1016 B app budget).
CALL_AUDIO_MAX_MULAW_LAN = 480
CALL_AUDIO_MAX_MULAW_SERIAL = 160

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
    mulaw_bytes,
    *,
    call_id="00000000-000",
    seq=9999,
    codec="audio/pcmulaw;rate=16000",
    msg_id="abcd1234efgh",
    link_mtu=1064,
):
    """Return encoded ChatMessage size for a μ-law audio payload."""
    data_b64 = base64.b64encode(bytes([0x7F]) * mulaw_bytes).decode("ascii")
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


def max_mulaw_bytes_for_mtu(link_mtu=1064, codec="audio/pcmulaw;rate=16000"):
    """Largest μ-law payload that fits the call-audio JSON envelope."""
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


def parse_call_payload(content):
    if not content:
        return {}
    try:
        data = json.loads(content)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


class VoiceCallSession:
    """Tracks one duplex call per messaging backend."""

    def __init__(self):
        self.state = STATE_IDLE
        self.call_id = ""
        self.peer_hash = ""
        self.transport = ""
        self.started_at = 0.0
        self._audio_seq_out = 0

    def reset(self):
        self.state = STATE_IDLE
        self.call_id = ""
        self.peer_hash = ""
        self.transport = ""
        self.started_at = 0.0
        self._audio_seq_out = 0

    def is_busy(self):
        return self.state in (STATE_OUTGOING, STATE_INCOMING, STATE_ACTIVE)

    def begin_outgoing(self, peer_hash, transport="lan"):
        self.reset()
        self.state = STATE_OUTGOING
        self.call_id = new_call_id()
        self.peer_hash = (peer_hash or "").replace(":", "")
        self.transport = (transport or "lan").strip().lower() or "lan"
        return self.call_id

    def begin_incoming(self, call_id, peer_hash, transport="lan"):
        self.reset()
        self.state = STATE_INCOMING
        self.call_id = (call_id or new_call_id()).strip()
        self.peer_hash = (peer_hash or "").replace(":", "")
        self.transport = (transport or "lan").strip().lower() or "lan"

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