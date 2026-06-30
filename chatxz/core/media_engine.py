"""Media engine — Rust-backed when available, Python fallback otherwise."""

from __future__ import annotations

import struct
import time
from collections import deque
from typing import Optional

MAGIC = b"CXMZ"
HEADER_SIZE = 17
MAX_PAYLOAD = 1200
FRAME_BYTES = 960 * 2  # 20ms mono int16 @ 48kHz

KIND_AUDIO = 1
KIND_VIDEO = 2
KIND_SCREEN = 3
KIND_CONTROL = 4

_rust_available = False
_rust_module = None

try:
    import chatxz_media as _rust_module  # type: ignore
    _rust_available = True
except ImportError:
    pass


def rust_available() -> bool:
    return _rust_available


def is_media_packet(data: bytes) -> bool:
    if _rust_available:
        return _rust_module.is_media_packet(data)
    return len(data) >= HEADER_SIZE and data[:4] == MAGIC


def parse_packet(data: bytes) -> Optional[tuple]:
    if _rust_available:
        return _rust_module.parse_packet(data)
    if len(data) < HEADER_SIZE or data[:4] != MAGIC:
        return None
    kind = data[5]
    flags = data[6]
    seq = struct.unpack(">I", data[7:11])[0]
    ts = struct.unpack(">I", data[11:15])[0]
    plen = struct.unpack(">H", data[15:17])[0]
    if len(data) < HEADER_SIZE + plen:
        return None
    return kind, flags, seq, ts, data[HEADER_SIZE : HEADER_SIZE + plen]


def _encode_packet(kind: int, flags: int, seq: int, ts: int, payload: bytes) -> bytes:
    plen = min(len(payload), MAX_PAYLOAD)
    hdr = MAGIC + bytes([1, kind, flags])
    hdr += struct.pack(">IIH", seq, ts, plen)
    return hdr + payload[:plen]


class _PythonJitter:
    def __init__(self):
        self._packets: dict[int, tuple] = {}
        self._next = 0
        self._target_ms = 60

    def reset(self):
        self._packets.clear()
        self._next = 0
        self._target_ms = 60

    def push(self, kind, flags, seq, ts, payload):
        self._packets[seq] = (kind, flags, ts, payload)
        while len(self._packets) > 64:
            k = min(self._packets)
            del self._packets[k]
            self._next = k + 1

    def pop(self, now_ms: int):
        if not self._packets:
            return None
        oldest_ts = min(v[2] for v in self._packets.values())
        if now_ms - oldest_ts < self._target_ms and len(self._packets) < 3:
            return None
        seq = self._next if self._next in self._packets else min(self._packets)
        kind, flags, ts, payload = self._packets.pop(seq)
        self._next = seq + 1
        return kind, flags, seq, ts, payload


class MediaSession:
    """Unified media session — delegates to Rust when built."""

    def __init__(self):
        self._rust = _rust_module.MediaSession() if _rust_available else None
        self._tx_seq = 0
        self._jitter = _PythonJitter()

    def reset(self):
        self._tx_seq = 0
        if self._rust:
            self._rust.reset()
        else:
            self._jitter.reset()

    def encode_audio_frame(self, pcm: bytes) -> bytes:
        if self._rust:
            return bytes(self._rust.encode_audio_frame(pcm))
        return pcm[: min(len(pcm), MAX_PAYLOAD)]

    def decode_audio_frame(self, opus: bytes) -> bytes:
        if self._rust:
            return bytes(self._rust.decode_audio_frame(opus))
        return opus

    def packetize_audio(self, payload: bytes, timestamp_ms: int) -> bytes:
        if self._rust:
            return bytes(self._rust.packetize_audio(payload, timestamp_ms))
        pkt = _encode_packet(KIND_AUDIO, 0, self._tx_seq, timestamp_ms, payload)
        self._tx_seq = (self._tx_seq + 1) & 0xFFFFFFFF
        return pkt

    def packetize_video(self, payload: bytes, timestamp_ms: int, keyframe: bool = False) -> bytes:
        if self._rust:
            return bytes(self._rust.packetize_video(payload, timestamp_ms, keyframe))
        flags = 1 if keyframe else 0
        pkt = _encode_packet(KIND_VIDEO, flags, self._tx_seq, timestamp_ms, payload)
        self._tx_seq = (self._tx_seq + 1) & 0xFFFFFFFF
        return pkt

    def packetize_screen(self, payload: bytes, timestamp_ms: int, keyframe: bool = False) -> bytes:
        if self._rust:
            return bytes(self._rust.packetize_screen(payload, timestamp_ms, keyframe))
        flags = 1 if keyframe else 0
        pkt = _encode_packet(KIND_SCREEN, flags, self._tx_seq, timestamp_ms, payload)
        self._tx_seq = (self._tx_seq + 1) & 0xFFFFFFFF
        return pkt

    def ingest_packet(self, data: bytes) -> Optional[tuple]:
        if self._rust:
            r = self._rust.ingest_packet(data)
            return r
        parsed = parse_packet(data)
        if not parsed:
            return None
        kind, flags, seq, ts, payload = parsed
        self._jitter.push(kind, flags, seq, ts, payload)
        return parsed

    def pop_audio(self, now_ms: Optional[int] = None) -> Optional[tuple]:
        now = now_ms if now_ms is not None else int(time.time() * 1000) & 0xFFFFFFFF
        if self._rust:
            r = self._rust.pop_audio(now)
            return r
        item = self._jitter.pop(now)
        if not item or item[0] != KIND_AUDIO:
            return None
        _, _, _, _, payload = item
        return payload, payload

    def jitter_depth(self) -> int:
        if self._rust:
            return self._rust.jitter_depth()
        return len(self._jitter._packets)

    def jitter_delay_ms(self) -> int:
        if self._rust:
            return self._rust.jitter_delay_ms()
        return self._jitter._target_ms