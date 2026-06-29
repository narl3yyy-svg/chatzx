from chatxz.core.audio import VoiceCallAudio, call_audio_available
from chatxz.core.audio.devices import (
    pcm_peak,
    pulse_best_capture_source,
    score_device,
)
from chatxz.core.audio.engine import CallAudioEngine
from chatxz.core.audio.jitter import SILENCE_PCM, VoiceJitterBuffer
from chatxz.core.audio.opus import OpusDecoder, OpusEncoder, opus_available


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
    assert CallAudioEngine is VoiceCallAudio


def test_pulse_best_capture_source_skips_monitor(monkeypatch):
    def fake_list():
        return [
            "alsa_output.pci.hdmi-stereo.monitor",
            "alsa_input.pci.analog-stereo",
            "alsa_output.pci.analog-stereo.monitor",
        ]

    from chatxz.core.audio import devices

    monkeypatch.setattr(devices, "pulse_list_sources", fake_list)
    assert pulse_best_capture_source() == "alsa_input.pci.analog-stereo"


def test_score_device_ignores_monitor_pulse_default():
    pulse = "alsa_output.pci.hdmi-stereo.monitor"
    mic = score_device(
        "HDA Intel PCH: ALC897 Analog (hw:0,0)", input_device=True, pulse_name=pulse
    )
    hdmi = score_device(
        "HDA Intel PCH: HDMI (hw:0,7)", input_device=True, pulse_name=pulse
    )
    assert mic > hdmi


def test_score_device_rejects_monitor_loopback():
    assert score_device("alsa_output.monitor", input_device=True, pulse_name=None) == -1000
    assert score_device("Null Audio Device", input_device=True, pulse_name=None) == -1000


def test_score_device_prefers_pulse_default_source():
    pulse = "alsa_input.usb_mic"
    assert score_device("alsa_output.usb_mic.monitor", input_device=True, pulse_name=pulse) == -1000
    mic = score_device("alsa_input.usb_mic", input_device=True, pulse_name=pulse)
    hdmi = score_device("HDA Intel HDMI", input_device=True, pulse_name=pulse)
    assert mic > hdmi


def test_score_device_prefers_default_on_alsa_without_pulse():
    default = score_device("default", input_device=True, pulse_name=None)
    alt = score_device(
        "HDA Intel PCH: ALC897 Alt Analog (hw:0,2)", input_device=True, pulse_name=None
    )
    hw = score_device(
        "HDA Intel PCH: ALC897 Analog (hw:0,0)", input_device=True, pulse_name=None
    )
    assert default > alt > hw


def test_score_device_prefers_default_input_over_raw_hw():
    default = score_device("default", input_device=True, pulse_name=None)
    hw = score_device(
        "HDA Intel PCH: ALC897 Analog (hw:0,0)", input_device=True, pulse_name=None
    )
    assert default > hw


def test_score_device_prefers_pipewire_input():
    pipe = score_device("pipewire", input_device=True, pulse_name=None)
    hw = score_device(
        "HDA Intel PCH: ALC897 Analog (hw:0,0)", input_device=True, pulse_name=None
    )
    assert pipe > hw


def test_score_device_output_penalizes_hdmi():
    speaker = score_device("Built-in Analog Output", input_device=False, pulse_name=None)
    hdmi = score_device("HDA Intel HDMI 7.1", input_device=False, pulse_name=None)
    assert speaker > hdmi


def test_jitter_buffer_seq_zero_buffered_ms():
    jb = VoiceJitterBuffer(target_frames=2, min_frames=2)
    frame = b"\x00\x01" * 960
    jb.push(0, frame)
    jb.push(1, frame)
    assert jb.buffered_ms == 40


def test_jitter_buffer_out_of_order_reorder():
    jb = VoiceJitterBuffer(target_frames=2, min_frames=2)
    f1 = b"\x01\x00" * 960
    f2 = b"\x02\x00" * 960
    f3 = b"\x03\x00" * 960
    jb.push(3, f3)
    assert jb.read() == SILENCE_PCM
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
    pcm = dec.decode(pkt)
    assert pcm
    jb.push(1, pcm)
    jb.push(2, pcm)
    assert jb._primed
    out = jb.read()
    assert out and len(out) == len(pcm)
    assert jb.read() == pcm
    assert sent == []


def test_pcm_peak():
    assert pcm_peak(SILENCE_PCM) == 0
    assert pcm_peak(b"\xff\x7f" + b"\x00\x00" * 959) > 0


def test_engine_stop_fast_does_not_block():
    engine = CallAudioEngine(lambda *_: True)
    engine.stop_fast()
    assert not engine._recv_ready.is_set()


def test_prepare_linux_audio_no_crash():
    from chatxz.core.audio.devices import prepare_linux_audio
    prepare_linux_audio()


def test_score_device_prefers_hw_when_pulse_bypass():
    from chatxz.core.audio import devices

    devices._PULSE_CAPTURE_BYPASS = True
    try:
        alt = devices.score_device(
            "HDA Intel PCH: ALC897 Alt Analog (hw:0,2)",
            input_device=True,
            pulse_name=None,
            pulse_bypass=True,
        )
        default = devices.score_device(
            "default", input_device=True, pulse_name=None, pulse_bypass=True
        )
        assert alt > default
    finally:
        devices._PULSE_CAPTURE_BYPASS = False


def test_pulse_best_capture_source_skips_monitor_only(monkeypatch):
    from chatxz.core.audio import devices

    monkeypatch.setattr(
        devices,
        "pulse_list_sources",
        lambda: ["alsa_output.pci.hdmi-stereo.monitor"],
    )
    assert devices.pulse_best_capture_source() is None