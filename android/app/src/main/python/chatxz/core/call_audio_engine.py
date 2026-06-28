"""Native call audio: Opus (aiortc) + PyAudio I/O with jitter buffer and PLC."""

from __future__ import annotations

import base64
import struct
import threading
import time
from typing import Callable, Optional

OPUS_SAMPLE_RATE = 48000
OPUS_FRAME_SAMPLES = 960  # 20 ms @ 48 kHz
OPUS_CODEC = "audio/opus;rate=48000;frame=20"
OPUS_PREFETCH_FRAMES = 3
OPUS_MAX_BUFFER_FRAMES = 64
SILENCE_PCM = b"\x00" * (OPUS_FRAME_SAMPLES * 2)


def call_audio_available() -> bool:
    try:
        import pyaudio  # noqa: F401
        from aiortc.codecs.opus import OpusEncoder  # noqa: F401
        return True
    except ImportError:
        return False


class CallJitterBuffer:
    """Sequence-ordered playout buffer with basic PLC (repeat last good frame)."""

    def __init__(
        self,
        prefetch_frames: int = OPUS_PREFETCH_FRAMES,
        max_frames: int = OPUS_MAX_BUFFER_FRAMES,
    ):
        self.prefetch_frames = max(1, int(prefetch_frames))
        self.max_frames = max(8, int(max_frames))
        self._frames: dict[int, bytes] = {}
        self._next_seq: Optional[int] = None
        self._last_pcm = SILENCE_PCM
        self._primed = False
        self._plc_count = 0
        self._lock = threading.Lock()

    def reset(self):
        with self._lock:
            self._frames.clear()
            self._next_seq = None
            self._last_pcm = SILENCE_PCM
            self._primed = False
            self._plc_count = 0

    @property
    def buffered_ms(self) -> int:
        with self._lock:
            return int(len(self._frames) * 20)

    @property
    def plc_frames(self) -> int:
        with self._lock:
            return self._plc_count

    def push(self, seq: int, pcm_s16: bytes) -> None:
        if not pcm_s16:
            return
        with self._lock:
            if self._next_seq is None:
                self._next_seq = int(seq)
            self._frames[int(seq)] = pcm_s16
            while len(self._frames) > self.max_frames:
                oldest = min(self._frames)
                del self._frames[oldest]
                if self._next_seq is not None and self._next_seq <= oldest:
                    self._next_seq = oldest + 1
            if not self._primed and self._next_seq is not None:
                available = sum(
                    1 for s in range(self._next_seq, self._next_seq + self.prefetch_frames)
                    if s in self._frames
                )
                if available >= self.prefetch_frames:
                    self._primed = True

    def read(self) -> bytes:
        with self._lock:
            if not self._primed or self._next_seq is None:
                return SILENCE_PCM
            seq = self._next_seq
            pcm = self._frames.pop(seq, None)
            self._next_seq = seq + 1
            if pcm:
                self._last_pcm = pcm
                return pcm
            self._plc_count += 1
            return self._last_pcm


class OpusCallCodec:
    """Encode/decode 20 ms Opus frames via aiortc."""

    def __init__(self):
        from aiortc.codecs.opus import OpusDecoder, OpusEncoder, SAMPLE_RATE, TIME_BASE
        from aiortc.jitterbuffer import JitterFrame

        self._time_base = TIME_BASE
        self._sample_rate = SAMPLE_RATE
        self._JitterFrame = JitterFrame
        self._encoder = OpusEncoder()
        self._decoder = OpusDecoder()
        self._decoder.codec.layout = "mono"
        self._pts = 0
        self._enc_ts = 0

    def encode_pcm(self, pcm_s16_mono: bytes) -> Optional[bytes]:
        from av import AudioFrame

        if len(pcm_s16_mono) < OPUS_FRAME_SAMPLES * 2:
            return None
        frame = AudioFrame(format="s16", layout="mono", samples=OPUS_FRAME_SAMPLES)
        frame.sample_rate = self._sample_rate
        frame.pts = self._pts
        frame.time_base = self._time_base
        frame.planes[0].update(pcm_s16_mono[: OPUS_FRAME_SAMPLES * 2])
        self._pts += OPUS_FRAME_SAMPLES
        packets, ts = self._encoder.encode(frame)
        if ts is not None:
            self._enc_ts = ts
        return packets[0] if packets else None

    def decode_opus(self, opus_bytes: bytes, timestamp: int = 0) -> Optional[bytes]:
        if not opus_bytes:
            return None
        jf = self._JitterFrame(data=opus_bytes, timestamp=timestamp)
        frames = self._decoder.decode(jf)
        if not frames:
            return None
        pcm = bytes(frames[0].planes[0])
        if frames[0].layout.name == "stereo" and len(pcm) >= OPUS_FRAME_SAMPLES * 4:
            shorts = struct.unpack(f"<{OPUS_FRAME_SAMPLES * 2}h", pcm[: OPUS_FRAME_SAMPLES * 4])
            mono = []
            for i in range(0, len(shorts), 2):
                mono.append((shorts[i] + shorts[i + 1]) // 2)
            return struct.pack(f"<{len(mono)}h", *mono)
        return pcm[: OPUS_FRAME_SAMPLES * 2]


class CallAudioEngine:
    """Duplex call audio using PyAudio callbacks and Opus over RNS."""

    def __init__(self, send_fn: Callable[[str, str], bool]):
        self._send_fn = send_fn
        self._codec = OpusCallCodec()
        self._jitter = CallJitterBuffer()
        self._seq_out = 0
        self._seq_in = 0
        self._running = False
        self._pa = None
        self._in_stream = None
        self._out_stream = None
        self._send_enabled = False
        self._lock = threading.Lock()
        self.frames_sent = 0
        self.frames_recv = 0

    @staticmethod
    def available() -> bool:
        return call_audio_available()

    def start(self) -> bool:
        if self._running:
            return True
        if not self.available():
            return False
        import pyaudio

        self._jitter.reset()
        self._seq_out = 0
        self._seq_in = 0
        self.frames_sent = 0
        self.frames_recv = 0
        self._pa = pyaudio.PyAudio()
        in_dev, in_name = self._pick_input_device(self._pa)
        fmt = pyaudio.paInt16
        channels = 1
        rate = OPUS_SAMPLE_RATE
        frame_count = OPUS_FRAME_SAMPLES

        self._mic_diag = 12
        self._silent_frames = 0

        def input_cb(in_data, frame_count, time_info, status):
            if not self._running or not self._send_enabled:
                return (None, pyaudio.paComplete)
            peak = self._pcm_peak(in_data)
            if self._mic_diag > 0:
                self._mic_diag -= 1
                print(f"[call-audio] mic peak {peak}")
            if peak < 32:
                self._silent_frames += 1
                return (None, pyaudio.paContinue)
            self._silent_frames = 0
            opus = self._codec.encode_pcm(in_data)
            if opus:
                self._seq_out += 1
                b64 = base64.b64encode(opus).decode("ascii")
                if self._send_fn(b64, OPUS_CODEC):
                    self.frames_sent += 1
                    if self.frames_sent <= 2 or self.frames_sent % 40 == 0:
                        print(
                            f"[call-audio] Native out #{self.frames_sent} "
                            f"({len(b64)} b64, peak {peak})"
                        )
            return (None, pyaudio.paContinue)

        def output_cb(in_data, frame_count, time_info, status):
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
                    print(f"[call-audio] Mic device: {in_name}")
            except Exception as mic_err:
                print(f"[call-audio] No capture device ({mic_err}) — receive-only playback")
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
            print(f"[call-audio] Native Opus engine started ({mode}, 48 kHz, 20 ms frames)")
            return True
        except Exception as e:
            print(f"[call-audio] Engine start failed: {e}")
            self.stop()
            return False

    def stop(self):
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
        self._jitter.reset()
        print("[call-audio] Native Opus engine stopped")

    def receive_frame(self, seq: int, audio_b64: str, codec: str = OPUS_CODEC) -> None:
        if not self._running or not audio_b64:
            return
        try:
            raw = base64.b64decode(audio_b64)
        except Exception:
            return
        pcm = None
        if "opus" in (codec or ""):
            pcm = self._codec.decode_opus(raw, int(seq or 0) * OPUS_FRAME_SAMPLES)
        elif "pcmulaw" in (codec or ""):
            pcm = self._mulaw_to_pcm(raw)
        elif (codec or "").startswith("audio/pcm"):
            pcm = raw[: OPUS_FRAME_SAMPLES * 2]
        if pcm:
            self._jitter.push(int(seq or 0), pcm)
            self.frames_recv += 1
            self._seq_in = max(self._seq_in, int(seq or 0))

    @staticmethod
    def _mulaw_to_pcm(mulaw_bytes: bytes) -> bytes:
        out = []
        for b in mulaw_bytes:
            u = (~b) & 0xFF
            sign = u & 0x80
            exp = (u >> 4) & 0x07
            mant = u & 0x0F
            sample = ((mant << 3) + 0x84) << exp
            sample -= 0x84
            if sign:
                sample = -sample
            out.append(max(-32768, min(32767, sample)))
        pcm = struct.pack(f"<{len(out)}h", *out)
        if len(out) < OPUS_FRAME_SAMPLES:
            pcm += SILENCE_PCM[: (OPUS_FRAME_SAMPLES - len(out)) * 2]
        return pcm[: OPUS_FRAME_SAMPLES * 2]

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

    @staticmethod
    def _pcm_has_audio(pcm_s16: bytes, threshold: int = 48) -> bool:
        return CallAudioEngine._pcm_peak(pcm_s16) >= threshold

    def stats(self) -> dict:
        return {
            "engine": "native-opus",
            "codec": OPUS_CODEC,
            "mode": "duplex" if self._send_enabled else "receive-only",
            "frames_sent": self.frames_sent,
            "frames_recv": self.frames_recv,
            "jitter_ms": self._jitter.buffered_ms,
            "plc_frames": self._jitter.plc_frames,
            "running": self._running,
        }