from chatxz.core.opus_native import (
    OPUS_FRAME_SAMPLES,
    OpusDecoder,
    OpusEncoder,
    opus_available,
)
from chatxz.core.voice_jitter_buffer import VoiceJitterBuffer, SILENCE_PCM


def test_opus_roundtrip():
    if not opus_available():
        return
    enc = OpusEncoder()
    dec = OpusDecoder()
    pcm = b"\x00\x01" * OPUS_FRAME_SAMPLES
    pkt = enc.encode(pcm)
    assert pkt and len(pkt) > 0
    out = dec.decode(pkt)
    assert out and len(out) == OPUS_FRAME_SAMPLES * 2


def test_voice_jitter_adaptive_delay():
    jb = VoiceJitterBuffer(target_frames=3, min_frames=2, max_frames=8)
    frame = b"\x00\x00" * OPUS_FRAME_SAMPLES
    jb.push(1, frame)
    jb.push(2, frame)
    assert jb.read() == SILENCE_PCM
    jb.push(3, frame)
    assert jb.read() == frame
    assert jb.buffered_ms >= 0