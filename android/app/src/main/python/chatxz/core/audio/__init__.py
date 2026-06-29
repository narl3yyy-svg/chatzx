"""Voice call stack over RNS — Opus 48 kHz / 20 ms frames.

Audio flow:
  capture → Opus encode → CALL_AUDIO packets (messaging)
  CALL_AUDIO receive → jitter buffer → Opus decode → playback

Signaling (invite/accept/end) lives in messaging; media in engine + jitter.
"""

from chatxz.core.audio.engine import CallAudioEngine, VoiceCallAudio, call_audio_available
from chatxz.core.audio.jitter import SILENCE_PCM, VoiceJitterBuffer
from chatxz.core.audio.session import (
    CALL_ACCEPT,
    CALL_AUDIO,
    CALL_END,
    CALL_INVITE,
    CALL_REJECT,
    CALL_TYPES,
    STATE_ACTIVE,
    STATE_IDLE,
    STATE_INCOMING,
    STATE_OUTGOING,
    VoiceCallSession,
    new_call_id,
    parse_call_payload,
    split_call_audio_b64,
)
from chatxz.core.opus_native import OPUS_CODEC, OPUS_FRAME_SAMPLES, OPUS_SAMPLE_RATE

__all__ = [
    "CALL_ACCEPT",
    "CALL_AUDIO",
    "CALL_END",
    "CALL_INVITE",
    "CALL_REJECT",
    "CALL_TYPES",
    "CallAudioEngine",
    "OPUS_CODEC",
    "OPUS_FRAME_SAMPLES",
    "OPUS_SAMPLE_RATE",
    "SILENCE_PCM",
    "STATE_ACTIVE",
    "STATE_IDLE",
    "STATE_INCOMING",
    "STATE_OUTGOING",
    "VoiceCallAudio",
    "VoiceCallSession",
    "VoiceJitterBuffer",
    "call_audio_available",
    "new_call_id",
    "parse_call_payload",
    "split_call_audio_b64",
]