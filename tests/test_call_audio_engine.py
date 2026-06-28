from chatxz.core.call_audio_engine import CallJitterBuffer, SILENCE_PCM, call_audio_available


def test_call_jitter_buffer_plc():
    jb = CallJitterBuffer(prefetch_frames=2)
    frame = b"\x01\x00" * 960
    jb.push(1, frame)
    jb.push(2, frame)
    assert jb.read() == frame
    assert jb.read() == frame
    plc = jb.read()
    assert plc == frame
    assert jb.plc_frames >= 1


def test_call_jitter_buffer_prefetch():
    jb = CallJitterBuffer(prefetch_frames=3)
    assert jb.read() == SILENCE_PCM
    frame = b"\x00\x00" * 960
    jb.push(10, frame)
    jb.push(11, frame)
    assert jb.read() == SILENCE_PCM
    jb.push(12, frame)
    assert jb.read() == frame


def test_call_audio_available_reports_bool():
    assert isinstance(call_audio_available(), bool)