"""Duplex voice call audio: libopus + PyAudio callbacks + adaptive jitter buffer."""

from __future__ import annotations

import base64
import struct
import threading
import time
from typing import Callable, Optional

from chatxz.core.opus_native import (
    OPUS_CODEC,
    OPUS_FRAME_SAMPLES,
    OPUS_SAMPLE_RATE,
    OpusDecoder,
    OpusEncoder,
    opus_available,
    opus_unavailable_reason,
)
from chatxz.core.voice_jitter_buffer import SILENCE_PCM, VoiceJitterBuffer

# Backward-compatible aliases
CallJitterBuffer = VoiceJitterBuffer
OPUS_PREFETCH_FRAMES = 4
OPUS_MAX_BUFFER_FRAMES = 12


def call_audio_available() -> bool:
    if not opus_available():
        return False
    try:
        import pyaudio  # noqa: F401
        return True
    except ImportError:
        return False


class VoiceCallAudio:
    """Capture → Opus encode → RNS send; receive → jitter buffer → playback."""

    def __init__(self, send_fn: Callable[[str, str], bool]):
        self._send_fn = send_fn
        self._encoder: Optional[OpusEncoder] = None
        self._decoder: Optional[OpusDecoder] = None
        self._jitter = VoiceJitterBuffer()
        self._running = False
        self._pa = None
        self._in_stream = None
        self._out_stream = None
        self._send_enabled = False
        self.frames_sent = 0
        self.frames_recv = 0
        self._mic_diag = 0

    @staticmethod
    def available() -> bool:
        return call_audio_available()

    def start(self) -> bool:
        if self._running:
            return True
        if not self.available():
            reason = opus_unavailable_reason() or "pyaudio missing"
            print(f"[call-audio] Native unavailable ({reason})")
            return False
        import pyaudio

        try:
            self._encoder = OpusEncoder()
            self._decoder = OpusDecoder()
        except Exception as e:
            print(f"[call-audio] Opus init failed: {e}")
            return False

        self._jitter.reset()
        self.frames_sent = 0
        self.frames_recv = 0
        self._mic_diag = 8
        self._pa = pyaudio.PyAudio()
        in_dev, in_name = self._pick_input_device(self._pa)
        fmt = pyaudio.paInt16
        channels = 1
        rate = OPUS_SAMPLE_RATE
        frame_count = OPUS_FRAME_SAMPLES

        def input_cb(in_data, _frame_count, _time_info, _status):
            if not self._running or not self._send_enabled or not self._encoder:
                return (None, pyaudio.paComplete)
            if self._mic_diag > 0:
                self._mic_diag -= 1
                print(f"[call-audio] mic peak {self._pcm_peak(in_data)}")
            opus = self._encoder.encode(in_data)
            if opus:
                b64 = base64.b64encode(opus).decode("ascii")
                if self._send_fn(b64, OPUS_CODEC):
                    self.frames_sent += 1
                    if self.frames_sent <= 3 or self.frames_sent % 50 == 0:
                        print(
                            f"[call-audio] Opus out #{self.frames_sent} "
                            f"({len(b64)} b64, {len(opus)} B)"
                        )
            return (None, pyaudio.paContinue)

        def output_cb(_in_data, _frame_count, _time_info, _status):
            if not self._running:
                return (SILENCE_PCM, pyaudio.paComplete)
            return (self._jitter.read(), pyaudio.paContinue)

        self._send_enabled = False
        try:
            try:
                open_kw = dict(
                    format=fmt,
                    channels=channels,
                    rate=rate,
                    input=True,
                    frames_per_buffer=frame_count,
                    stream_callback=input_cb,
                )
                if in_dev is not None:
                    open_kw["input_device_index"] = in_dev
                self._in_stream = self._pa.open(**open_kw)
                self._send_enabled = True
                if in_name:
                    print(f"[call-audio] Mic: {in_name}")
            except Exception as mic_err:
                print(f"[call-audio] No capture ({mic_err}) — receive-only")
                self._in_stream = None
            self._out_stream = self._pa.open(
                format=fmt,
                channels=channels,
                rate=rate,
                output=True,
                frames_per_buffer=frame_count,
                stream_callback=output_cb,
            )
            self._running = True
            if self._in_stream:
                self._in_stream.start_stream()
            self._out_stream.start_stream()
            mode = "duplex" if self._send_enabled else "receive-only"
            print(f"[call-audio] Voice engine started ({mode}, Opus 48 kHz, 20 ms)")
            return True
        except Exception as e:
            print(f"[call-audio] Engine start failed: {e}")
            self.stop()
            return False

    def stop(self) -> None:
        self._running = False
        for stream in (self._in_stream, self._out_stream):
            if stream:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
        self._in_stream = None
        self._out_stream = None
        if self._pa:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None
        for codec in (self._encoder, self._decoder):
            if codec:
                try:
                    codec.close()
                except Exception:
                    pass
        self._encoder = None
        self._decoder = None
        self._jitter.reset()
        print("[call-audio] Voice engine stopped")

    def receive_frame(self, seq: int, audio_b64: str, codec: str = OPUS_CODEC) -> None:
        if not self._running or not audio_b64 or not self._decoder:
            return
        if "opus" not in (codec or "").lower():
            return
        try:
            raw = base64.b64decode(audio_b64)
        except Exception:
            return
        pcm = self._decoder.decode(raw)
        if pcm:
            self._jitter.push(int(seq or 0), pcm)
            self.frames_recv += 1

    @staticmethod
    def _pick_input_device(pa):
        try:
            info = pa.get_default_input_device_info()
            name = str(info.get("name") or "")
            low = name.lower()
            if "monitor" not in low and "loopback" not in low and "null" not in low:
                return int(info["index"]), name or "default"
        except Exception:
            pass
        try:
            for i in range(pa.get_device_count()):
                info = pa.get_device_info_by_index(i)
                if int(info.get("maxInputChannels", 0) or 0) < 1:
                    continue
                name = str(info.get("name") or "").lower()
                if "monitor" in name or "loopback" in name or "null" in name:
                    continue
                return i, str(info.get("name") or f"device {i}")
        except Exception:
            pass
        return None, None

    @staticmethod
    def _pcm_peak(pcm_s16: bytes) -> int:
        if len(pcm_s16) < 2:
            return 0
        count = len(pcm_s16) // 2
        samples = struct.unpack(f"<{count}h", pcm_s16[: count * 2])
        return max(abs(s) for s in samples) if samples else 0

    def stats(self) -> dict:
        jb = self._jitter.stats()
        return {
            "engine": "native-opus",
            "codec": OPUS_CODEC,
            "mode": "duplex" if self._send_enabled else "receive-only",
            "frames_sent": self.frames_sent,
            "frames_recv": self.frames_recv,
            "jitter_ms": jb.get("buffered_ms", 0),
            "playout_delay_ms": jb.get("playout_delay_ms", 0),
            "plc_frames": jb.get("plc_frames", 0),
            "running": self._running,
        }


# Backward-compatible name used by server
CallAudioEngine = VoiceCallAudio


# Legacy shim — tests referenced OpusCallCodec
class OpusCallCodec:
    def __init__(self):
        self._enc = OpusEncoder()
        self._dec = OpusDecoder()

    def encode_pcm(self, pcm_s16_mono: bytes):
        return self._enc.encode(pcm_s16_mono)

    def decode_opus(self, opus_bytes: bytes, _timestamp: int = 0):
        return self._dec.decode(opus_bytes)