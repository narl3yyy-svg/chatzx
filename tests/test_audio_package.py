def test_audio_package_exports():
    from chatxz.core import audio

    assert audio.OPUS_CODEC
    assert audio.VoiceCallSession
    assert audio.VoiceJitterBuffer
    assert audio.call_audio_available is not None