"""Direct libopus bindings via ctypes — no aiortc/WebRTC."""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import struct
import subprocess
import sys
from typing import List, Optional

OPUS_OK = 0
OPUS_APPLICATION_VOIP = 2048
OPUS_SAMPLE_RATE = 48000
OPUS_CHANNELS = 1
OPUS_FRAME_SAMPLES = 960  # 20 ms @ 48 kHz
OPUS_MAX_PACKET = 4000
OPUS_BITRATE = 32000
OPUS_CODEC = "audio/opus;rate=48000;frame=20"

_lib: Optional[ctypes.CDLL] = None
_lib_error: Optional[str] = None


def _macos_brew_opus_paths() -> List[str]:
    paths: List[str] = []
    try:
        proc = subprocess.run(
            ["brew", "--prefix", "opus"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            prefix = (proc.stdout or "").strip()
            if prefix:
                lib_dir = os.path.join(prefix, "lib")
                for name in ("libopus.0.dylib", "libopus.dylib"):
                    paths.append(os.path.join(lib_dir, name))
    except Exception:
        pass
    return paths


def _opus_library_candidates() -> List[str]:
    """Platform-specific libopus paths (Windows .dll, macOS .dylib, Linux .so)."""
    candidates: List[str] = []

    roots = [
        os.path.join(os.path.dirname(__file__), "..", "native", "windows"),
        os.path.join(os.path.dirname(__file__), "..", "native", "macos"),
        os.path.join(os.path.dirname(__file__), "..", "native", "linux"),
        os.path.join(os.path.dirname(__file__), "..", "native"),
        os.path.dirname(__file__),
    ]
    if hasattr(sys, "prefix"):
        roots.append(os.path.join(sys.prefix, "DLLs"))
        roots.append(os.path.join(sys.prefix, "lib"))
    for env_key in ("CHATXZ_ROOT", "VIRTUAL_ENV"):
        root = os.environ.get(env_key, "").strip()
        if root:
            roots.append(root)
            if sys.platform == "win32":
                roots.append(os.path.join(root, "Scripts"))
            else:
                roots.append(os.path.join(root, "lib"))

    if sys.platform == "win32":
        candidates.extend(("opus.dll", "libopus-0.dll", "libopus.dll", "opus"))
    elif sys.platform == "darwin":
        candidates.extend(_macos_brew_opus_paths())
        candidates.extend((
            "/opt/homebrew/lib/libopus.0.dylib",
            "/opt/homebrew/lib/libopus.dylib",
            "/usr/local/lib/libopus.0.dylib",
            "/usr/local/lib/libopus.dylib",
            "libopus.0.dylib",
            "libopus.dylib",
            "opus",
        ))
    else:
        candidates.extend(("libopus.so.0", "libopus.so", "opus"))

    found = ctypes.util.find_library("opus")
    if found and os.path.isfile(found):
        candidates.append(found)

    seen = set()
    ordered: List[str] = []
    for root in roots:
        if not root:
            continue
        root = os.path.abspath(root)
        for name in ("opus.dll", "libopus-0.dll", "libopus.dll",
                     "libopus.0.dylib", "libopus.dylib",
                     "libopus.so.0", "libopus.so"):
            path = os.path.join(root, name)
            if path not in seen and os.path.isfile(path):
                seen.add(path)
                ordered.append(path)
    for item in candidates:
        if not item or item in seen:
            continue
        if os.path.isabs(item) or item.startswith((".", "/")):
            if not os.path.isfile(item):
                continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _load_libopus() -> Optional[ctypes.CDLL]:
    global _lib, _lib_error
    if _lib is not None:
        return _lib
    if _lib_error:
        return None
    last_err = None
    for name in _opus_library_candidates():
        try:
            _lib = ctypes.CDLL(name)
            return _lib
        except OSError as e:
            last_err = e
    _lib_error = str(last_err or "libopus not found")
    return None


def opus_available() -> bool:
    return _load_libopus() is not None


def opus_unavailable_reason() -> str:
    _load_libopus()
    return _lib_error or ""


class OpusEncoder:
    """Encode 20 ms mono PCM (s16le) frames to Opus packets."""

    def __init__(self, sample_rate: int = OPUS_SAMPLE_RATE, channels: int = OPUS_CHANNELS):
        lib = _load_libopus()
        if not lib:
            raise RuntimeError(f"libopus unavailable: {opus_unavailable_reason()}")
        lib.opus_encoder_create.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int),
        ]
        lib.opus_encoder_create.restype = ctypes.c_void_p
        lib.opus_encoder_destroy.argtypes = [ctypes.c_void_p]
        lib.opus_encoder_destroy.restype = None
        lib.opus_encode.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int16),
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_char),
            ctypes.c_int32,
        ]
        lib.opus_encode.restype = ctypes.c_int32
        lib.opus_encoder_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        lib.opus_encoder_ctl.restype = ctypes.c_int

        err = ctypes.c_int()
        self._enc = lib.opus_encoder_create(sample_rate, channels, OPUS_APPLICATION_VOIP, ctypes.byref(err))
        if not self._enc or err.value != OPUS_OK:
            raise RuntimeError(f"opus_encoder_create failed ({err.value})")
        self._lib = lib
        self._sample_rate = sample_rate
        self._channels = channels
        self._set_int_ctl(4002, OPUS_BITRATE)  # OPUS_SET_BITRATE
        self._set_int_ctl(4010, 0)  # OPUS_SET_DTX
        self._set_int_ctl(4008, 1)  # OPUS_SET_VBR

    def _set_int_ctl(self, req: int, value: int) -> None:
        self._lib.opus_encoder_ctl(self._enc, req, value)

    def encode(self, pcm_s16_mono: bytes) -> Optional[bytes]:
        need = OPUS_FRAME_SAMPLES * self._channels * 2
        if len(pcm_s16_mono) < need:
            return None
        pcm = pcm_s16_mono[:need]
        count = OPUS_FRAME_SAMPLES * self._channels
        buf_type = ctypes.c_int16 * count
        pcm_ptr = buf_type.from_buffer_copy(pcm)
        out = (ctypes.c_char * OPUS_MAX_PACKET)()
        n = self._lib.opus_encode(
            self._enc,
            pcm_ptr,
            OPUS_FRAME_SAMPLES,
            out,
            OPUS_MAX_PACKET,
        )
        if n <= 0:
            return None
        return bytes(out[:n])

    def close(self) -> None:
        if self._enc:
            self._lib.opus_encoder_destroy(self._enc)
            self._enc = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


class OpusDecoder:
    """Decode Opus packets to 20 ms mono PCM (s16le)."""

    def __init__(self, sample_rate: int = OPUS_SAMPLE_RATE, channels: int = OPUS_CHANNELS):
        lib = _load_libopus()
        if not lib:
            raise RuntimeError(f"libopus unavailable: {opus_unavailable_reason()}")
        lib.opus_decoder_create.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int),
        ]
        lib.opus_decoder_create.restype = ctypes.c_void_p
        lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]
        lib.opus_decoder_destroy.restype = None
        lib.opus_decode.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_char),
            ctypes.c_int32,
            ctypes.POINTER(ctypes.c_int16),
            ctypes.c_int,
            ctypes.c_int,
        ]
        lib.opus_decode.restype = ctypes.c_int32

        err = ctypes.c_int()
        self._dec = lib.opus_decoder_create(sample_rate, channels, ctypes.byref(err))
        if not self._dec or err.value != OPUS_OK:
            raise RuntimeError(f"opus_decoder_create failed ({err.value})")
        self._lib = lib
        self._channels = channels

    def decode(self, opus_bytes: bytes, fec: bool = False) -> Optional[bytes]:
        if not opus_bytes:
            return None
        in_buf = (ctypes.c_char * len(opus_bytes)).from_buffer_copy(opus_bytes)
        out_count = OPUS_FRAME_SAMPLES * self._channels
        out_type = ctypes.c_int16 * out_count
        out_buf = out_type()
        n = self._lib.opus_decode(
            self._dec,
            in_buf,
            len(opus_bytes),
            out_buf,
            OPUS_FRAME_SAMPLES,
            1 if fec else 0,
        )
        if n <= 0:
            return None
        byte_len = n * self._channels * 2
        return ctypes.string_at(ctypes.addressof(out_buf), byte_len)

    def close(self) -> None:
        if self._dec:
            self._lib.opus_decoder_destroy(self._dec)
            self._dec = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass