from chatxz.core.call_audio_engine import VoiceCallAudio, call_audio_available
from chatxz.core.opus_native import OpusDecoder, OpusEncoder, opus_available
from chatxz.core.voice_jitter_buffer import SILENCE_PCM, VoiceJitterBuffer


def test_call_jitter_buffer_plc():
    jb = VoiceJitterBuffer(min_frames=2, target_frames=2)
    frame = b"\x01\x00" * 960
    jb.push(1, frame)
    jb.push(2, frame)
    assert jb.read() == frame
    assert jb.read() == frame
    plc = jb.read()
    assert plc != SILENCE_PCM
    assert len(plc) == len(frame)
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


def test_opus_roundtrip_via_engine_codecs():
    if not opus_available():
        return
    enc = OpusEncoder()
    dec = OpusDecoder()
    pkt = enc.encode(SILENCE_PCM)
    assert pkt and len(pkt) >= 1
    pcm = dec.decode(pkt)
    assert pcm and len(pcm) == 960 * 2


def test_voice_call_audio_class_exists():
    assert VoiceCallAudio.available() == call_audio_available()


def test_pulse_best_capture_source_skips_monitor(monkeypatch):
    def fake_list():
        return [
            "alsa_output.pci.hdmi-stereo.monitor",
            "alsa_input.pci.analog-stereo",
            "alsa_output.pci.analog-stereo.monitor",
        ]
    monkeypatch.setattr(VoiceCallAudio, "_pulse_list_sources", staticmethod(fake_list))
    assert VoiceCallAudio._pulse_best_capture_source() == "alsa_input.pci.analog-stereo"


def test_score_device_ignores_monitor_pulse_default():
    pulse = "alsa_output.pci.hdmi-stereo.monitor"
    mic = VoiceCallAudio._score_device(
        "HDA Intel PCH: ALC897 Analog (hw:0,0)", input_device=True, pulse_name=pulse
    )
    hdmi = VoiceCallAudio._score_device(
        "HDA Intel PCH: HDMI (hw:0,7)", input_device=True, pulse_name=pulse
    )
    assert mic > hdmi


def test_score_device_rejects_monitor_loopback():
    score = VoiceCallAudio._score_device("alsa_output.monitor", input_device=True, pulse_name=None)
    assert score == -1000
    score = VoiceCallAudio._score_device("Null Audio Device", input_device=True, pulse_name=None)
    assert score == -1000


def test_score_device_prefers_pulse_default_source():
    pulse = "alsa_input.usb_mic"
    monitor = VoiceCallAudio._score_device(
        "alsa_output.usb_mic.monitor", input_device=True, pulse_name=pulse
    )
    mic = VoiceCallAudio._score_device(
        "alsa_input.usb_mic", input_device=True, pulse_name=pulse
    )
    hdmi = VoiceCallAudio._score_device(
        "HDA Intel HDMI", input_device=True, pulse_name=pulse
    )
    assert mic > hdmi
    assert monitor == -1000


def test_score_device_output_penalizes_hdmi():
    speaker = VoiceCallAudio._score_device("Built-in Analog Output", input_device=False, pulse_name=None)
    hdmi = VoiceCallAudio._score_device("HDA Intel HDMI 7.1", input_device=False, pulse_name=None)
    assert speaker > hdmi


def test_jitter_buffer_seq_zero_buffered_ms():
    jb = VoiceJitterBuffer(target_frames=2, min_frames=2)
    frame = b"\x00\x01" * 960
    jb.push(0, frame)
    jb.push(1, frame)
    # seq 0 is valid; buffered_ms must reflect 2 frames ahead of playout (40 ms)
    assert jb.buffered_ms == 40


def test_jitter_buffer_out_of_order_reorder():
    jb = VoiceJitterBuffer(target_frames=2, min_frames=2)
    f1 = b"\x01\x00" * 960
    f2 = b"\x02\x00" * 960
    f3 = b"\x03\x00" * 960
    jb.push(3, f3)
    assert jb.read() == SILENCE_PCM  # not primed yet
    jb.push(1, f1)
    jb.push(2, f2)
    assert jb.read() == f1
    assert jb.read() == f2
    assert jb.read() == f3


def test_opus_encode_send_receive_pipeline():
    if not opus_available():
        return
    import base64
    sent = []
    engine = VoiceCallAudio(lambda b64, codec: sent.append((b64, codec)) or True)
    enc = OpusEncoder()
    dec = OpusDecoder()
    jb = VoiceJitterBuffer(target_frames=2, min_frames=2)
    tone = b"\x00\x10" * 960
    pkt = enc.encode(tone)
    assert pkt
    b64 = base64.b64encode(pkt).decode("ascii")
    pcm = dec.decode(pkt)
    assert pcm
    jb.push(1, pcm)
    jb.push(2, pcm)
    assert jb._primed
    out = jb.read()
    assert out and len(out) == len(pcm)
    assert jb.read() == pcm
    assert sent == []