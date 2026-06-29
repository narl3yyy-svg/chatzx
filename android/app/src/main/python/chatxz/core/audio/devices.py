"""Microphone and speaker device selection for desktop voice calls.

Linux (Arch/Ubuntu): uses pactl to avoid HDMI/monitor capture sources and maps
PulseAudio default source to the best PyAudio input index.
"""

from __future__ import annotations

import struct
import subprocess
import sys
from typing import List, Optional, Tuple


def pcm_peak(pcm_s16: bytes) -> int:
    if len(pcm_s16) < 2:
        return 0
    count = len(pcm_s16) // 2
    samples = struct.unpack(f"<{count}h", pcm_s16[: count * 2])
    return max(abs(s) for s in samples) if samples else 0


def pulse_list_sources() -> List[str]:
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


def pulse_best_capture_source() -> Optional[str]:
    sources = pulse_list_sources()
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


def pulse_default_source() -> Optional[str]:
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
    return pulse_best_capture_source()


def ensure_pulse_capture_source() -> Optional[str]:
    """If Pulse default is a monitor, switch to a real microphone source."""
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
        current = (proc.stdout or "").strip() if proc.returncode == 0 else ""
        if current and ".monitor" not in current.lower():
            return current
        best = pulse_best_capture_source()
        if best:
            subprocess.run(
                ["pactl", "set-default-source", best],
                capture_output=True,
                timeout=2,
                check=False,
            )
            print(f"[call-audio] Pulse default source → {best}")
            return best
    except Exception:
        pass
    return pulse_default_source()


def alsa_prepare_capture() -> None:
    """Unmute ALSA capture on bare-metal Linux (no PulseAudio)."""
    if sys.platform not in ("linux", "linux2"):
        return
    cmds = [
        ["amixer", "-c", "0", "sset", "Capture", "cap"],
        ["amixer", "-c", "0", "sset", "Capture", "90%"],
        ["amixer", "-c", "0", "sset", "Master", "90%"],
        ["amixer", "-c", "0", "sset", "Headphone", "90%"],
        ["amixer", "-c", "0", "sset", "Speaker", "90%"],
    ]
    for cmd in cmds:
        try:
            subprocess.run(cmd, capture_output=True, timeout=2, check=False)
        except Exception:
            pass


def prepare_linux_audio() -> None:
    """Best-effort mic/speaker prep before opening PyAudio streams."""
    if sys.platform not in ("linux", "linux2"):
        return
    pulse = ensure_pulse_capture_source()
    if not pulse:
        alsa_prepare_capture()
        print("[call-audio] ALSA-only audio — unmuted capture, using default device")


def score_device(name: str, *, input_device: bool, pulse_name: Optional[str]) -> int:
    low = (name or "").lower()
    if not low:
        return -1000
    if any(x in low for x in ("monitor", "loopback", "null", "dummy")):
        return -1000
    if input_device and "alt analog" in low:
        return 94 if pulse_name else 90
    if low in ("default", "sysdefault"):
        # Prefer ALSA plug default when PulseAudio is unavailable.
        return 96 if input_device and not pulse_name else 88 if input_device else 25
    score = 20
    if input_device:
        if "pipewire" in low:
            score += 75
    if pulse_name and ".monitor" not in pulse_name.lower():
        pulse_low = pulse_name.lower()
        if pulse_low in low or low in pulse_low:
            score += 120
        pulse_base = pulse_low.split(".monitor")[0]
        if pulse_base and pulse_base in low:
            score += 80
    if input_device:
        for kw in (
            "microphone", "mic", "headset", "headphone", "webcam", "usb",
            "built-in", "internal", "audio-in", "capture", "analog",
        ):
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


def rank_devices(pa, *, input_device: bool) -> List[Tuple[int, str, int]]:
    pulse = pulse_default_source() if input_device else None
    if input_device:
        ensure_pulse_capture_source()
        pulse = pulse_default_source()
    ranked: List[Tuple[int, str, int]] = []
    try:
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            ch_key = "maxInputChannels" if input_device else "maxOutputChannels"
            if int(info.get(ch_key, 0) or 0) < 1:
                continue
            name = str(info.get("name") or f"device {i}")
            score = score_device(name, input_device=input_device, pulse_name=pulse)
            if score > -1000:
                ranked.append((score, i, name))
    except Exception:
        pass
    ranked.sort(key=lambda t: t[0], reverse=True)
    return [(idx, name, score) for score, idx, name in ranked]


def probe_input_device(pa, fmt, rate: int, frame_count: int) -> Tuple[Optional[int], Optional[str], List[Tuple[int, str, int]]]:
    """Return (index, name, full_ranked_list). Picks best device even without probe signal."""
    ranked = rank_devices(pa, input_device=True)
    if not ranked:
        return None, None, []
    # Prefer default/sysdefault first on ALSA-only systems (routes through plug).
    default_pick = next((t for t in ranked if t[1].lower() in ("default", "sysdefault")), None)
    best_silent = default_pick or ranked[0]
    for idx, name, score in ranked[:6]:
        stream = None
        try:
            stream = pa.open(
                format=fmt,
                channels=1,
                rate=rate,
                input=True,
                frames_per_buffer=frame_count,
                input_device_index=idx,
            )
            stream.start_stream()
            peak = 0
            for _ in range(4):
                data = stream.read(frame_count, exception_on_overflow=False)
                peak = max(peak, pcm_peak(data))
            print(f"[call-audio] Probe [{idx}] peak={peak}: {name[:72]}")
            if peak > 60:
                print(f"[call-audio] Selected input [{idx}] score={score}: {name}")
                return idx, name, ranked
        except Exception as exc:
            print(f"[call-audio] Probe [{idx}] failed: {exc}")
        finally:
            if stream:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
    idx, name, score = best_silent
    print(
        f"[call-audio] Selected input [{idx}] score={score}: {name} "
        "(no probe signal — hot-swap if silent)"
    )
    return idx, name, ranked


def pick_output_device(pa) -> Tuple[Optional[int], Optional[str]]:
    ranked = rank_devices(pa, input_device=False)
    if ranked:
        idx, name, score = ranked[0]
        print(f"[call-audio] Selected output [{idx}] score={score}: {name}")
        return idx, name
    return None, None


def log_audio_devices(pa) -> None:
    try:
        pulse = pulse_default_source()
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