"""Desktop voice call audio engine.

Pipeline (duplex):
  capture thread → Opus encode → send_fn → RNS CALL_AUDIO packets
  receive_frame → Opus decode → jitter buffer → playback thread → speaker

Playback/decoding start immediately; mic capture opens in parallel so incoming
audio is not blocked by slow ALSA device probing on Linux.
"""

from __future__ import annotations

import base64
import threading
import time
from typing import Callable, List, Optional, Tuple

from chatxz.core.audio.devices import (
    log_audio_devices,
    pcm_peak,
    pick_output_device,
    prepare_linux_audio,
    probe_input_device,
)
from chatxz.core.audio.jitter import SILENCE_PCM, VoiceJitterBuffer
from chatxz.core.audio.opus import (
    OPUS_CODEC,
    OPUS_FRAME_SAMPLES,
    OPUS_SAMPLE_RATE,
    OpusDecoder,
    OpusEncoder,
    opus_available,
    opus_unavailable_reason,
)

SILENT_FRAMES_BEFORE_HOTSWAP = 250  # ~5 s
MAX_HOTSWAPS = 2
FRAME_INTERVAL_SEC = OPUS_FRAME_SAMPLES / OPUS_SAMPLE_RATE


def call_audio_available() -> bool:
    if not opus_available():
        return False
    try:
        import pyaudio  # noqa: F401
        return True
    except ImportError:
        return False


class CallAudioEngine:
    """Capture → Opus → RNS; receive → jitter → decode → playback."""

    def __init__(self, send_fn: Callable[[str, str], bool]):
        self._send_fn = send_fn
        self._encoder: Optional[OpusEncoder] = None
        self._decoder: Optional[OpusDecoder] = None
        self._jitter = VoiceJitterBuffer()
        self._stop = threading.Event()
        self._active = threading.Event()
        self._recv_ready = threading.Event()
        self._pa = None
        self._in_stream = None
        self._out_stream = None
        self._capture_thread: Optional[threading.Thread] = None
        self._playback_thread: Optional[threading.Thread] = None
        self._cleanup_thread: Optional[threading.Thread] = None
        self._send_enabled = False
        self.frames_sent = 0
        self.frames_recv = 0
        self._mic_diag = 0
        self._peak_max = 0
        self._silent_frames = 0
        self._hotswap_count = 0
        self._recv_log = 5
        self._decode_fail_log = 5
        self._input_ranked: List[Tuple[int, str, int]] = []
        self._input_rank_pos = 0
        self._in_dev: Optional[int] = None
        self._in_name: Optional[str] = None
        self._lock = threading.Lock()

    @staticmethod
    def available() -> bool:
        return call_audio_available()

    def start(self) -> bool:
        with self._lock:
            if self._active.is_set() or self._recv_ready.is_set():
                return True
            return self._start_unlocked()

    def _start_unlocked(self) -> bool:
        if not self.available():
            reason = opus_unavailable_reason() or "pyaudio missing"
            print(f"[call-audio] Native unavailable ({reason})")
            return False
        import pyaudio

        prepare_linux_audio()

        try:
            self._encoder = OpusEncoder()
            self._decoder = OpusDecoder()
        except Exception as e:
            print(f"[call-audio] Opus init failed: {e}")
            return False

        self._stop.clear()
        self._jitter.reset()
        self.frames_sent = 0
        self.frames_recv = 0
        self._mic_diag = 8
        self._peak_max = 0
        self._silent_frames = 0
        self._hotswap_count = 0
        self._recv_log = 5
        self._decode_fail_log = 5
        self._input_rank_pos = 0
        self._send_enabled = False

        self._pa = pyaudio.PyAudio()
        log_audio_devices(self._pa)

        fmt = pyaudio.paInt16
        channels = 1
        rate = OPUS_SAMPLE_RATE
        frame_count = OPUS_FRAME_SAMPLES

        out_dev, out_name = pick_output_device(self._pa)
        try:
            out_kw = dict(
                format=fmt,
                channels=channels,
                rate=rate,
                output=True,
                frames_per_buffer=frame_count,
            )
            if out_dev is not None:
                out_kw["output_device_index"] = out_dev
            self._out_stream = self._pa.open(**out_kw)
            if out_name:
                print(f"[call-audio] Speaker: {out_name}")
            self._out_stream.start_stream()

            self._recv_ready.set()
            self._active.set()
            self._playback_thread = threading.Thread(
                target=self._playback_loop,
                name="call-audio-playback",
                daemon=True,
            )
            self._playback_thread.start()

            self._in_dev, self._in_name, self._input_ranked = probe_input_device(
                self._pa, fmt, rate, frame_count
            )
            if self._in_dev is not None:
                self._in_stream = self._pa.open(
                    format=fmt,
                    channels=channels,
                    rate=rate,
                    input=True,
                    frames_per_buffer=frame_count,
                    input_device_index=self._in_dev,
                )
                self._in_stream.start_stream()
                self._send_enabled = True
                print(f"[call-audio] Mic: {self._in_name}")
                self._capture_thread = threading.Thread(
                    target=self._capture_loop,
                    name="call-audio-capture",
                    daemon=True,
                )
                self._capture_thread.start()
            else:
                print("[call-audio] No capture device — receive-only")

            mode = "duplex" if self._send_enabled else "receive-only"
            print(f"[call-audio] Engine started ({mode}, Opus 48 kHz, 20 ms)")
            return True
        except Exception as e:
            print(f"[call-audio] Engine start failed: {e}")
            self._stop_unlocked(blocking=False)
            return False

    def stop(self, *, blocking: bool = True) -> None:
        """Thread-safe. Use blocking=False from signal handlers."""
        self.stop_fast()
        if blocking:
            self._join_cleanup(timeout=1.0)

    def stop_fast(self) -> None:
        """Signal-safe: set flags immediately, cleanup in background."""
        self._stop.set()
        self._active.clear()
        self._recv_ready.clear()
        self._send_enabled = False
        with self._lock:
            if self._cleanup_thread and self._cleanup_thread.is_alive():
                return
            self._cleanup_thread = threading.Thread(
                target=self._stop_unlocked,
                kwargs={"blocking": False},
                name="call-audio-cleanup",
                daemon=True,
            )
            self._cleanup_thread.start()

    def _join_cleanup(self, timeout: float = 1.0) -> None:
        th = self._cleanup_thread
        if th and th.is_alive():
            th.join(timeout=timeout)

    def _stop_unlocked(self, *, blocking: bool = True) -> None:
        self._stop.set()
        self._active.clear()
        self._recv_ready.clear()
        self._send_enabled = False
        if blocking:
            for th in (self._capture_thread, self._playback_thread):
                if th and th.is_alive():
                    th.join(timeout=0.3)
        self._capture_thread = None
        self._playback_thread = None
        for stream in (self._in_stream, self._out_stream):
            if stream:
                try:
                    stream.stop_stream()
                except Exception:
                    pass
                try:
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
        print("[call-audio] Engine stopped")

    def _capture_loop(self) -> None:
        frame_count = OPUS_FRAME_SAMPLES
        next_tick = time.monotonic()
        while not self._stop.is_set() and self._active.is_set():
            stream = self._in_stream
            enc = self._encoder
            if not stream or not enc or not self._send_enabled:
                break
            try:
                in_data = stream.read(frame_count, exception_on_overflow=False)
            except Exception:
                break
            peak = pcm_peak(in_data)
            self._peak_max = max(self._peak_max, peak)
            if peak < 40:
                self._silent_frames += 1
            else:
                self._silent_frames = 0
            if self._mic_diag > 0:
                self._mic_diag -= 1
                print(f"[call-audio] mic peak {peak}")
            if (
                self._silent_frames >= SILENT_FRAMES_BEFORE_HOTSWAP
                and self._hotswap_count < MAX_HOTSWAPS
                and self._input_ranked
                and len(self._input_ranked) > 1
            ):
                if self._try_hotswap_input():
                    self._hotswap_count += 1
                self._silent_frames = 0
            opus = enc.encode(in_data)
            if opus and self._send_fn and not self._stop.is_set():
                b64 = base64.b64encode(opus).decode("ascii")
                if self._send_fn(b64, OPUS_CODEC):
                    self.frames_sent += 1
                    if self.frames_sent <= 3 or self.frames_sent % 50 == 0:
                        print(
                            f"[call-audio] Opus out #{self.frames_sent} "
                            f"({len(b64)} b64, {len(opus)} B)"
                        )
            next_tick += FRAME_INTERVAL_SEC
            sleep_for = next_tick - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)
            else:
                next_tick = time.monotonic()

    def _playback_loop(self) -> None:
        next_tick = time.monotonic()
        while not self._stop.is_set() and self._recv_ready.is_set():
            stream = self._out_stream
            if not stream:
                break
            pcm = self._jitter.read()
            try:
                stream.write(pcm or SILENCE_PCM, exception_on_underflow=False)
            except Exception as exc:
                if self._recv_log > 0:
                    print(f"[call-audio] Playback write failed: {exc}")
                    self._recv_log -= 1
                break
            next_tick += FRAME_INTERVAL_SEC
            sleep_for = next_tick - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)
            else:
                next_tick = time.monotonic()

    def _try_hotswap_input(self) -> bool:
        if not self._pa or not self._input_ranked:
            return False
        import pyaudio

        for _ in range(len(self._input_ranked)):
            self._input_rank_pos = (self._input_rank_pos + 1) % len(self._input_ranked)
            idx, name, score = self._input_ranked[self._input_rank_pos]
            if idx == self._in_dev:
                continue
            old = self._in_stream
            try:
                new_stream = self._pa.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=OPUS_SAMPLE_RATE,
                    input=True,
                    frames_per_buffer=OPUS_FRAME_SAMPLES,
                    input_device_index=idx,
                )
                new_stream.start_stream()
                self._in_stream = new_stream
                self._in_dev = idx
                self._in_name = name
                self._send_enabled = True
                print(f"[call-audio] Hot-swapped mic [{idx}] score={score}: {name[:72]}")
            except Exception as exc:
                print(f"[call-audio] Hot-swap failed [{idx}]: {exc}")
                continue
            if old:
                try:
                    old.stop_stream()
                    old.close()
                except Exception:
                    pass
            return True
        return False

    def receive_frame(self, seq: int, audio_b64: str, codec: str = OPUS_CODEC) -> None:
        if self._stop.is_set() or not audio_b64:
            return
        if not self._recv_ready.is_set():
            return
        dec = self._decoder
        if not dec:
            return
        if codec and "opus" not in (codec or "").lower():
            return
        try:
            raw = base64.b64decode(audio_b64)
        except Exception:
            return
        if not raw:
            return
        pcm = dec.decode(raw)
        if not pcm:
            if self._decode_fail_log > 0:
                self._decode_fail_log -= 1
                print(f"[call-audio] Opus decode failed (seq={seq}, {len(raw)} B)")
            return
        self._jitter.push(int(seq or 0), pcm)
        self.frames_recv += 1
        if self._recv_log > 0:
            self._recv_log -= 1
            print(
                f"[call-audio] Opus in #{self.frames_recv} "
                f"(seq={seq}, {len(audio_b64)} b64, jb={self._jitter.buffered_ms} ms)"
            )

    def stats(self) -> dict:
        jb = self._jitter.stats()
        return {
            "engine": "native-opus",
            "codec": OPUS_CODEC,
            "mode": "duplex" if self._send_enabled else "receive-only",
            "frames_sent": self.frames_sent,
            "frames_recv": self.frames_recv,
            "mic_peak_max": self._peak_max,
            "jitter_ms": jb.get("buffered_ms", 0),
            "playout_delay_ms": jb.get("playout_delay_ms", 0),
            "plc_frames": jb.get("plc_frames", 0),
            "running": self._recv_ready.is_set() and not self._stop.is_set(),
            "input_device": self._in_name or "",
        }


VoiceCallAudio = CallAudioEngine