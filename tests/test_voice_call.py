import json
import time

from chatxz.core.opus_native import OPUS_CODEC
from chatxz.core.voice_call import (
    CALL_ACCEPT,
    CALL_AUDIO,
    CALL_AUDIO_MAX_OPUS_BYTES,
    CALL_INVITE,
    CALL_TYPES,
    STATE_ACTIVE,
    STATE_IDLE,
    STATE_INCOMING,
    STATE_OUTGOING,
    VoiceCallSession,
    estimate_call_audio_packet_size,
    max_audio_bytes_for_mtu,
    new_call_id,
    parse_call_payload,
    split_call_audio_b64,
)


def test_call_types_include_signaling_and_audio():
    assert CALL_INVITE in CALL_TYPES
    assert CALL_ACCEPT in CALL_TYPES
    assert CALL_AUDIO in CALL_TYPES


def test_parse_call_payload_empty_and_invalid():
    assert parse_call_payload("") == {}
    assert parse_call_payload("not-json") == {}
    assert parse_call_payload("[]") == {}


def test_parse_call_payload_dict():
    raw = json.dumps({"call_id": "abc", "transport": "serial"})
    assert parse_call_payload(raw) == {"call_id": "abc", "transport": "serial"}


def test_new_call_id_length():
    cid = new_call_id()
    assert len(cid) == 12


def test_voice_call_session_outgoing_flow():
    vc = VoiceCallSession()
    assert vc.state == STATE_IDLE
    assert not vc.is_busy()

    peer = "aa" * 16
    cid = vc.begin_outgoing(peer, "lan")
    assert vc.state == STATE_OUTGOING
    assert vc.is_busy()
    assert vc.call_id == cid
    assert vc.peer_hash == peer
    assert vc.transport == "lan"

    assert vc.activate(cid)
    assert vc.state == STATE_ACTIVE
    assert vc.started_at > 0

    ended_id, ended_peer = vc.end()
    assert ended_id == cid
    assert ended_peer == peer
    assert vc.state == STATE_IDLE


def test_voice_call_session_incoming_activate_mismatch():
    vc = VoiceCallSession()
    vc.begin_incoming("call-1", "bb" * 16, "serial")
    assert vc.state == STATE_INCOMING
    assert not vc.activate("other-id")
    assert vc.state == STATE_INCOMING
    assert vc.activate("call-1")
    assert vc.state == STATE_ACTIVE


def test_split_call_audio_oversized_opus():
    big = split_call_audio_b64(
        __import__("base64").b64encode(bytes([0x7F]) * 900).decode("ascii"),
        OPUS_CODEC,
        call_id="1b0f674d-6d4",
        link_mtu=1064,
    )
    assert len(big) >= 2
    for chunk in big:
        raw_len = len(__import__("base64").b64decode(chunk))
        size, budget = estimate_call_audio_packet_size(raw_len, call_id="1b0f674d-6d4")
        assert size <= budget


def test_call_audio_opus_fits_rns_mtu():
    max_bytes = max_audio_bytes_for_mtu(1064)
    assert max_bytes >= CALL_AUDIO_MAX_OPUS_BYTES
    size, budget = estimate_call_audio_packet_size(CALL_AUDIO_MAX_OPUS_BYTES)
    assert size <= budget
    over, _ = estimate_call_audio_packet_size(900)
    assert over > budget


def test_voice_call_audio_seq_increments():
    vc = VoiceCallSession()
    vc.begin_outgoing("cc" * 16)
    vc.activate()
    assert vc.next_audio_seq() == 1
    assert vc.next_audio_seq() == 2


def test_voice_call_stale_outgoing():
    vc = VoiceCallSession()
    vc.begin_outgoing("dd" * 16)
    vc.state_since = time.time() - (VoiceCallSession.STALE_OUTGOING_SEC + 1)
    assert vc.is_stale()
    assert vc.is_busy()


def test_voice_call_not_stale_when_active_recent():
    vc = VoiceCallSession()
    vc.begin_outgoing("ee" * 16)
    vc.activate()
    assert not vc.is_stale()


def test_call_id_matches_requires_nonempty_id():
    from chatxz.core.messaging import MessagingBackend

    mb = MessagingBackend.__new__(MessagingBackend)
    mb.voice_call = VoiceCallSession()
    mb.voice_call.begin_outgoing("ff" * 16)
    assert mb._call_id_matches("") is False
    assert mb._call_id_matches(mb.voice_call.call_id) is True
    assert mb._call_id_matches("other-id") is False


def test_call_glare_we_win_lexicographic():
    from chatxz.core.messaging import MessagingBackend

    mb = MessagingBackend.__new__(MessagingBackend)
    mb.voice_call = VoiceCallSession()
    mb.voice_call.begin_outgoing("gg" * 16)
    assert mb._call_glare_we_win("zzzzzzzz-zzz") is True
    assert mb._call_glare_we_win("00000000-000") is False