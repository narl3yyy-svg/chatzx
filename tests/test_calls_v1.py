"""Tests for v1.0.x RNS call manager and media engine."""

import json
import struct

from chatxz.core.calls import (
    CALL_ACCEPT,
    CALL_BUSY,
    CALL_HANGUP,
    CALL_INVITE,
    CALL_REJECT,
    CallManager,
    CallState,
    MESSAGE_TYPE_CALL,
)
from chatxz.core.media_engine import (
    HEADER_SIZE,
    KIND_AUDIO,
    KIND_SCREEN,
    KIND_VIDEO,
    MAGIC,
    MediaSession,
    is_media_packet,
    parse_packet,
)


def _manager():
    signals = []
    media = []

    def send_signaling(peer, payload):
        signals.append((peer, payload))

    def send_media(peer, data):
        media.append((peer, data))

    mgr = CallManager(send_signaling, send_media, lambda _p: True)
    return mgr, signals, media


def test_call_manager_outgoing_invite():
    mgr, signals, _ = _manager()
    events = []
    mgr.set_event_handler(lambda ev, data: events.append((ev, data)))

    session = mgr.start_call("abc123", mode="video")
    assert session is not None
    assert session.state == CallState.OUTGOING
    assert session.mode.value == "video"
    assert any(ev == "outgoing" for ev, _ in events)

    peer, raw = signals[0]
    assert peer == "abc123"
    payload = json.loads(raw)
    assert payload["action"] == CALL_INVITE
    assert payload["mode"] == "video"
    assert payload["call_id"] == session.call_id


def test_call_manager_busy_when_already_in_call():
    mgr, signals, _ = _manager()
    assert mgr.start_call("peer1") is not None
    assert mgr.start_call("peer2") is None

    mgr2, signals2, _ = _manager()
    mgr2.set_event_handler(lambda *_: None)
    mgr2.start_call("caller")
    mgr2.handle_signaling("caller", json.dumps({"action": CALL_INVITE, "call_id": "other99", "mode": "audio"}))
    assert signals2[-1][0] == "caller"
    assert json.loads(signals2[-1][1])["action"] == CALL_BUSY


def test_call_manager_incoming_accept_flow():
    mgr, signals, _ = _manager()
    events = []
    mgr.set_event_handler(lambda ev, data: events.append((ev, data)))

    invite = json.dumps({"action": CALL_INVITE, "call_id": "call42", "mode": "audio"})
    mgr.handle_signaling("peer9", invite)
    assert mgr.active_session().state == CallState.INCOMING
    assert events[-1] == ("incoming", events[-1][1])
    assert events[-1][1]["call_id"] == "call42"

    assert mgr.accept_call("call42") is True
    assert mgr.active_session().state == CallState.ACTIVE
    assert events[-1][0] == "accepted"
    assert json.loads(signals[-1][1])["action"] == CALL_ACCEPT


def test_call_manager_reject_and_hangup():
    mgr, signals, _ = _manager()
    mgr.set_event_handler(lambda *_: None)
    mgr.start_call("peerA")
    call_id = mgr.active_session().call_id

    mgr.reject_call(call_id)
    assert mgr.active_session() is None
    assert json.loads(signals[-1][1])["action"] == CALL_REJECT

    mgr2, signals2, _ = _manager()
    mgr2.set_event_handler(lambda *_: None)
    mgr2.start_call("peerB")
    cid = mgr2.active_session().call_id
    accept = json.dumps({"action": CALL_ACCEPT, "call_id": cid})
    mgr2.handle_signaling("peerB", accept)
    mgr2.hangup()
    assert mgr2.active_session() is None
    assert json.loads(signals2[-1][1])["action"] == CALL_HANGUP


def test_call_manager_remote_hangup():
    mgr, _, _ = _manager()
    ended = []
    mgr.set_event_handler(lambda ev, data: ended.append((ev, data)))
    mgr.start_call("peerZ")
    call_id = mgr.active_session().call_id
    mgr.handle_signaling("peerZ", json.dumps({"action": CALL_HANGUP, "call_id": call_id}))
    assert mgr.active_session() is None
    assert ended[-1][0] == "ended"


def test_call_signaling_message_type_constant():
    assert MESSAGE_TYPE_CALL == "__call"


def test_media_packet_roundtrip_python_fallback():
    session = MediaSession()
    pcm = b"\x00\x01" * 480
    pkt = session.packetize_audio(pcm, timestamp_ms=1000)
    assert is_media_packet(pkt)
    parsed = parse_packet(pkt)
    assert parsed is not None
    kind, flags, seq, ts, payload = parsed
    assert kind == KIND_AUDIO
    assert flags == 0
    assert seq == 0
    assert ts == 1000
    assert payload == pcm


def test_media_video_and_screen_packet_kinds():
    session = MediaSession()
    jpg = b"\xff\xd8\xff" + b"x" * 32
    vp = session.packetize_video(jpg, timestamp_ms=42, keyframe=True)
    sp = session.packetize_screen(jpg, timestamp_ms=43, keyframe=False)
    assert parse_packet(vp)[0] == KIND_VIDEO
    assert parse_packet(vp)[1] == 1
    assert parse_packet(sp)[0] == KIND_SCREEN
    assert parse_packet(sp)[1] == 0


def test_media_ingest_and_pop_audio():
    session = MediaSession()
    pcm = b"\x7f\x00" * 120
    pkt = session.packetize_audio(pcm, timestamp_ms=5000)
    session.ingest_packet(pkt)
    out = session.pop_audio(now_ms=5100)
    assert out is not None
    decoded, _ = out
    assert decoded == pcm


def test_parse_packet_rejects_invalid():
    assert parse_packet(b"") is None
    assert parse_packet(b"NOPE") is None
    short = MAGIC + bytes([1, KIND_AUDIO, 0]) + struct.pack(">IIH", 0, 0, 10)
    assert parse_packet(short) is None