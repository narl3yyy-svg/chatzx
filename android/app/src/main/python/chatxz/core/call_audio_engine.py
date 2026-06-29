"""Desktop voice call audio: libopus encode/decode + PyAudio I/O + jitter buffer."""

from __future__ import annotations

import base64
import struct
import subprocess
import sys
from typing import Callable, List, Optional, Tuple

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


def call_audio_available() -> bool:
    if not opus_available():
        return False
    try:
        import pyaudio  # noqa: F401
        return True
    except ImportError:
        return False


class VoiceCallAudio:
    """Capture → Opus encode → send; receive → jitter buffer → playback."""

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
        self._peak_max = 0
        self._recv_log = 3

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
        self._peak_max = 0
        self._recv_log = 3
        self._pa = pyaudio.PyAudio()
        self._log_audio_devices(self._pa)

        in_dev, in_name = self._pick_input_device(self._pa)
        out_dev, out_name = self._pick_output_device(self._pa)
        fmt = pyaudio.paInt16
        channels = 1
        rate = OPUS_SAMPLE_RATE
        frame_count = OPUS_FRAME_SAMPLES

        def input_cb(in_data, _frame_count, _time_info, _status):
            if not self._running or not self._send_enabled or not self._encoder:
                return (None, pyaudio.paComplete)
            peak = self._pcm_peak(in_data)
            self._peak_max = max(self._peak_max, peak)
            if self._mic_diag > 0:
                self._mic_diag -= 1
                print(f"[call-audio] mic peak {peak}")
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
            out_kw = dict(
                format=fmt,
                channels=channels,
                rate=rate,
                output=True,
                frames_per_buffer=frame_count,
                stream_callback=output_cb,
            )
            if out_dev is not None:
                out_kw["output_device_index"] = out_dev
            self._out_stream = self._pa.open(**out_kw)
            if out_name:
                print(f"[call-audio] Speaker: {out_name}")
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
        self._send_enabled = False
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
            if self._recv_log > 0:
                self._recv_log -= 1
                print(
                    f"[call-audio] Opus in #{self.frames_recv} "
                    f"(seq={seq}, {len(audio_b64)} b64, jb={self._jitter.buffered_ms} ms)"
                )

    @staticmethod
    def _pulse_list_sources() -> List[str]:
        if sys.platform not in ("linux", "linux2"):
            return []
        try:
            proc = subprocess.run(
                ["pactl", "list", "short", "sources"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if proc.returncode != 0:
                return []
            names: List[str] = []
            for line in (proc.stdout or "").splitlines():
                parts = line.split("\t")
                if len(parts) >= 2:
                    name = parts[1].strip()
                    if name:
                        names.append(name)
            return names
        except Exception:
            return []

    @staticmethod
    def _pulse_best_capture_source() -> Optional[str]:
        sources = VoiceCallAudio._pulse_list_sources()
        ranked: List[Tuple[int, str]] = []
        for name in sources:
            low = name.lower()
            if ".monitor" in low or "monitor of" in low:
                continue
            score = 0
            if low.startswith("alsa_input"):
                score += 80
            for kw in ("microphone", "mic", "headset", "webcam", "usb", "analog"):
                if kw in low:
                    score += 20
            for kw in ("hdmi", "spdif", "output", "sink"):
                if kw in low:
                    score -= 40
            if score > -20:
                ranked.append((score, name))
        if not ranked:
            return None
        ranked.sort(key=lambda t: t[0], reverse=True)
        return ranked[0][1]

    @staticmethod
    def _pulse_default_source() -> Optional[str]:
        if sys.platform not in ("linux", "linux2"):
            return None
        try:
            proc = subprocess.run(
                ["pactl", "get-default-source"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if proc.returncode == 0:
                name = (proc.stdout or "").strip()
                if name and ".monitor" not in name.lower():
                    return name
        except Exception:
            pass
        return VoiceCallAudio._pulse_best_capture_source()

    @staticmethod
    def _score_device(name: str, *, input_device: bool, pulse_name: Optional[str]) -> int:
        low = (name or "").lower()
        if not low:
            return -1000
        if any(x in low for x in ("monitor", "loopback", "null", "dummy")):
            return -1000
        if low in ("default", "sysdefault"):
            return 1
        score = 20
        if pulse_name and ".monitor" not in pulse_name.lower():
            pulse_low = pulse_name.lower()
            if pulse_low in low or low in pulse_low:
                score += 120
            pulse_base = pulse_low.split(".monitor")[0]
            if pulse_base and pulse_base in low:
                score += 80
        if input_device:
            for kw in ("microphone", "mic", "headset", "headphone", "webcam", "usb",
                       "built-in", "internal", "audio-in", "capture"):
                if kw in low:
                    score += 25
            for kw in ("hdmi", "spdif", "speaker", "output", "sink"):
                if kw in low:
                    score -= 40
        else:
            for kw in ("speaker", "headphone", "headset", "analog", "usb", "built-in",
                       "internal", "audio-out"):
                if kw in low:
                    score += 20
            for kw in ("hdmi", "spdif", "monitor"):
                if kw in low:
                    score -= 30
        return score

    @classmethod
    def _rank_devices(cls, pa, *, input_device: bool) -> List[Tuple[int, str, int]]:
        pulse = cls._pulse_default_source() if input_device else None
        ranked: List[Tuple[int, str, int]] = []
        try:
            for i in range(pa.get_device_count()):
                info = pa.get_device_info_by_index(i)
                ch_key = "maxInputChannels" if input_device else "maxOutputChannels"
                if int(info.get(ch_key, 0) or 0) < 1:
                    continue
                name = str(info.get("name") or f"device {i}")
                score = cls._score_device(name, input_device=input_device, pulse_name=pulse)
                if score > -1000:
                    ranked.append((score, i, name))
        except Exception:
            pass
        ranked.sort(key=lambda t: t[0], reverse=True)
        return [(idx, name, score) for score, idx, name in ranked]

    @classmethod
    def _pick_input_device(cls, pa) -> Tuple[Optional[int], Optional[str]]:
        ranked = cls._rank_devices(pa, input_device=True)
        if ranked:
            idx, name, score = ranked[0]
            print(f"[call-audio] Selected input [{idx}] score={score}: {name}")
            return idx, name
        return None, None

    @classmethod
    def _pick_output_device(cls, pa) -> Tuple[Optional[int], Optional[str]]:
        ranked = cls._rank_devices(pa, input_device=False)
        if ranked:
            idx, name, score = ranked[0]
            print(f"[call-audio] Selected output [{idx}] score={score}: {name}")
            return idx, name
        return None, None

    @staticmethod
    def _log_audio_devices(pa) -> None:
        try:
            pulse = VoiceCallAudio._pulse_default_source()
            if pulse:
                print(f"[call-audio] PulseAudio default source: {pulse}")
            for i in range(min(pa.get_device_count(), 16)):
                info = pa.get_device_info_by_index(i)
                name = str(info.get("name") or "")
                ins = int(info.get("maxInputChannels", 0) or 0)
                outs = int(info.get("maxOutputChannels", 0) or 0)
                if ins or outs:
                    print(f"[call-audio] Device {i}: in={ins} out={outs} — {name[:72]}")
        except Exception:
            pass

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
            "mic_peak_max": self._peak_max,
            "jitter_ms": jb.get("buffered_ms", 0),
            "playout_delay_ms": jb.get("playout_delay_ms", 0),
            "plc_frames": jb.get("plc_frames", 0),
            "running": self._running,
        }


CallAudioEngine = VoiceCallAudio