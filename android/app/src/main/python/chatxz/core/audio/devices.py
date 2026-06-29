"""Microphone and speaker device selection for desktop voice calls.

Linux (Arch/Ubuntu): uses pactl when a real capture source exists; otherwise opens
direct ALSA hw devices (bypassing Pulse default/monitor routing).
"""

from __future__ import annotations

import struct
import subprocess
import sys
import threading
from typing import List, Optional, Tuple

# Set by prepare_linux_audio(): bypass Pulse default for PyAudio device ranking.
_PULSE_CAPTURE_BYPASS = False


def pcm_peak(pcm_s16: bytes) -> int:
    if len(pcm_s16) < 2:
        return 0
    count = len(pcm_s16) // 2
    samples = struct.unpack(f"<{count}h", pcm_s16[: count * 2])
    return max(abs(s) for s in samples) if samples else 0


def pulse_available() -> bool:
    if sys.platform not in ("linux", "linux2"):
        return False
    try:
        proc = subprocess.run(
            ["pactl", "info"],
            capture_output=True,
            timeout=2,
            check=False,
        )
        return proc.returncode == 0
    except Exception:
        return False


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


def pulse_list_sinks() -> List[str]:
    if sys.platform not in ("linux", "linux2"):
        return []
    try:
        proc = subprocess.run(
            ["pactl", "list", "short", "sinks"],
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


def pulse_best_playback_sink() -> Optional[str]:
    sinks = pulse_list_sinks()
    ranked: List[Tuple[int, str]] = []
    for name in sinks:
        low = name.lower()
        if ".monitor" in low:
            continue
        score = 0
        if low.startswith("alsa_output"):
            score += 60
        for kw in ("analog", "speaker", "headphone", "headset", "usb", "built-in"):
            if kw in low:
                score += 25
        for kw in ("hdmi", "spdif", "iec958"):
            if kw in low:
                score -= 50
        ranked.append((score, name))
    if not ranked:
        return None
    ranked.sort(key=lambda t: t[0], reverse=True)
    best_score, best = ranked[0]
    if best_score < 0:
        return None
    return best


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


def pulse_default_sink() -> Optional[str]:
    if sys.platform not in ("linux", "linux2"):
        return None
    try:
        proc = subprocess.run(
            ["pactl", "get-default-sink"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if proc.returncode == 0:
            return (proc.stdout or "").strip() or None
    except Exception:
        pass
    return None


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
        if current:
            print(
                f"[call-audio] Pulse has no mic source (default={current}) "
                "— using direct ALSA capture"
            )
    except Exception:
        pass
    return pulse_default_source()


def ensure_pulse_playback_sink() -> Optional[str]:
    """Prefer analog speaker output when Pulse default is HDMI."""
    if sys.platform not in ("linux", "linux2"):
        return None
    try:
        current = pulse_default_sink() or ""
        low = current.lower()
        if current and "hdmi" not in low and "spdif" not in low and "iec958" not in low:
            return current
        best = pulse_best_playback_sink()
        if best and best != current:
            subprocess.run(
                ["pactl", "set-default-sink", best],
                capture_output=True,
                timeout=2,
                check=False,
            )
            print(f"[call-audio] Pulse default sink → {best}")
            return best
    except Exception:
        pass
    return pulse_default_sink()


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


def pulse_capture_bypass() -> bool:
    return _PULSE_CAPTURE_BYPASS


def prepare_linux_audio() -> str:
    """Best-effort mic/speaker prep before opening PyAudio streams.

    Returns mode: pulse_ok | pulse_no_mic | alsa_only | other
    """
    global _PULSE_CAPTURE_BYPASS
    _PULSE_CAPTURE_BYPASS = False
    if sys.platform not in ("linux", "linux2"):
        return "other"
    if pulse_available():
        source = ensure_pulse_capture_source()
        ensure_pulse_playback_sink()
        if source and ".monitor" not in source.lower():
            print(f"[call-audio] PulseAudio capture: {source}")
            return "pulse_ok"
        _PULSE_CAPTURE_BYPASS = True
        print("[call-audio] PulseAudio running but no mic — direct ALSA capture")
        return "pulse_no_mic"
    alsa_prepare_capture()
    _PULSE_CAPTURE_BYPASS = True
    print("[call-audio] ALSA-only audio — unmuted capture, using hw devices")
    return "alsa_only"


def score_device(
    name: str,
    *,
    input_device: bool,
    pulse_name: Optional[str],
    pulse_bypass: bool = False,
) -> int:
    low = (name or "").lower()
    if not low:
        return -1000
    if any(x in low for x in ("monitor", "loopback", "null", "dummy")):
        return -1000
    bypass = pulse_bypass or pulse_capture_bypass()
    if input_device and bypass:
        if "alt analog" in low:
            return 98
        if "analog" in low and "hw:" in low:
            return 96
        if low in ("default", "sysdefault"):
            return 40
    if input_device and "alt analog" in low:
        return 94 if pulse_name else 90
    if low in ("default", "sysdefault"):
        if input_device and bypass:
            return 40
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
        if bypass and "hdmi" in low:
            score -= 60
        for kw in ("speaker", "headphone", "headset", "analog", "usb", "built-in",
                   "internal", "audio-out"):
            if kw in low:
                score += 20
        for kw in ("hdmi", "spdif", "monitor"):
            if kw in low:
                score -= 30
    return score


def rank_devices(pa, *, input_device: bool) -> List[Tuple[int, str, int]]:
    bypass = pulse_capture_bypass()
    pulse = None
    if input_device and not bypass:
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
            score = score_device(
                name,
                input_device=input_device,
                pulse_name=pulse,
                pulse_bypass=bypass,
            )
            if score > -1000:
                ranked.append((score, i, name))
    except Exception:
        pass
    ranked.sort(key=lambda t: t[0], reverse=True)
    return [(idx, name, score) for score, idx, name in ranked]


def _probe_read(stream, frame_count: int, timeout: float = 1.5) -> Tuple[Optional[bytes], int, Optional[str]]:
    """Read one probe buffer with a timeout so ALSA/Pulse cannot hang startup."""
    result: List[object] = [None, 0, None]

    def _read():
        try:
            data = stream.read(frame_count, exception_on_overflow=False)
            result[0] = data
            result[1] = pcm_peak(data)
        except Exception as exc:
            result[2] = str(exc)

    thread = threading.Thread(target=_read, name="call-audio-probe", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return None, -1, "timeout"
    if result[2]:
        return None, -1, str(result[2])
    return result[0], int(result[1] or 0), None


def pick_input_device(pa) -> Tuple[Optional[int], Optional[str], List[Tuple[int, str, int]]]:
    """Rank-only mic pick (no stream open). Safe when ALSA/Pulse is flaky."""
    ranked = rank_devices(pa, input_device=True)
    if not ranked:
        return None, None, []
    idx, name, score = ranked[0]
    print(f"[call-audio] Selected input [{idx}] score={score}: {name}")
    return idx, name, ranked


def create_pyaudio(timeout: float = 5.0):
    """Construct PyAudio in a helper thread — constructor can block on ALSA scan."""
    result: List[object] = [None, None]

    def _init():
        try:
            import pyaudio
            result[0] = pyaudio.PyAudio()
        except Exception as exc:
            result[1] = exc

    thread = threading.Thread(target=_init, name="call-audio-pa-init", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(f"PyAudio init timed out after {timeout}s")
    if result[1]:
        raise result[1]
    if not result[0]:
        raise RuntimeError("PyAudio init returned None")
    return result[0]


def _open_stream_with_timeout(pa, timeout: float = 3.0, **kwargs):
    """Open a PyAudio stream in a helper thread so ALSA cannot block forever."""
    result: List[object] = [None, None]

    def _open():
        try:
            result[0] = pa.open(**kwargs)
        except Exception as exc:
            result[1] = exc

    thread = threading.Thread(target=_open, name="call-audio-open", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(f"PyAudio open timed out after {timeout}s")
    if result[1]:
        raise result[1]
    return result[0]


def probe_input_device(
    pa,
    fmt,
    rate: int,
    frame_count: int,
    *,
    skip_probe: bool = False,
) -> Tuple[Optional[int], Optional[str], List[Tuple[int, str, int]]]:
    """Return (index, name, full_ranked_list). Picks best device even without probe signal."""
    ranked = rank_devices(pa, input_device=True)
    if not ranked:
        return None, None, []
    if skip_probe or pulse_capture_bypass():
        return pick_input_device(pa)
    bypass = pulse_capture_bypass()
    default_pick = None
    if not bypass:
        default_pick = next(
            (t for t in ranked if t[1].lower() in ("default", "sysdefault")),
            None,
        )
    best_silent = default_pick or ranked[0]
    for idx, name, score in ranked[:3]:
        stream = None
        try:
            stream = _open_stream_with_timeout(
                pa,
                timeout=2.0,
                format=fmt,
                channels=1,
                rate=rate,
                input=True,
                frames_per_buffer=frame_count,
                input_device_index=idx,
            )
            stream.start_stream()
            peak = 0
            timed_out = False
            for _ in range(4):
                data, sample_peak, err = _probe_read(stream, frame_count)
                if err == "timeout":
                    timed_out = True
                    print(f"[call-audio] Probe [{idx}] timed out: {name[:72]}")
                    break
                if err:
                    print(f"[call-audio] Probe [{idx}] failed: {err}")
                    break
                peak = max(peak, sample_peak)
            if timed_out:
                break
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
        sink = pulse_default_sink()
        if pulse:
            print(f"[call-audio] PulseAudio default source: {pulse}")
        if sink:
            print(f"[call-audio] PulseAudio default sink: {sink}")
        if pulse_capture_bypass():
            print("[call-audio] Pulse capture bypass active — ranking direct ALSA devices")
        for i in range(min(pa.get_device_count(), 16)):
            info = pa.get_device_info_by_index(i)
            name = str(info.get("name") or "")
            ins = int(info.get("maxInputChannels", 0) or 0)
            outs = int(info.get("maxOutputChannels", 0) or 0)
            if ins or outs:
                print(f"[call-audio] Device {i}: in={ins} out={outs} — {name[:72]}")
    except Exception:
        pass