from chatxz.core.call_audio_engine import (
    CallJitterBuffer,
    OpusCallCodec,
    SILENCE_PCM,
    VoiceJitterBuffer,
    call_audio_available,
)
from chatxz.core.opus_native import opus_available


def test_call_jitter_buffer_plc():
    jb = VoiceJitterBuffer(min_frames=2, target_frames=2)
    frame = b"\x01\x00" * 960
    jb.push(1, frame)
    jb.push(2, frame)
    assert jb.read() == frame
    assert jb.read() == frame
    plc = jb.read()
    assert plc == frame
    assert jb.plc_frames >= 1


def test_call_jitter_buffer_prefetch():
    jb = VoiceJitterBuffer(target_frames=3, min_frames=2)
    assert jb.read() == SILENCE_PCM
    frame = b"\x00\x00" * 960
    jb.push(10, frame)
    jb.push(11, frame)
    assert jb.read() == SILENCE_PCM
    jb.push(12, frame)
    assert jb.read() == frame


def test_call_audio_available_reports_bool():
    assert isinstance(call_audio_available(), bool)


def test_opus_codec_decodes_silence_frame():
    if not opus_available():
        return
    codec = OpusCallCodec()
    pkt = codec.encode_pcm(SILENCE_PCM)
    assert pkt and len(pkt) >= 1
    pcm = codec.decode_opus(pkt)
    assert pcm and len(pcm) == 960 * 2