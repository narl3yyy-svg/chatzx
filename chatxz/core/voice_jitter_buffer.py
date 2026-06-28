"""Adaptive playout jitter buffer for 20 ms Opus voice frames."""

from __future__ import annotations

import threading
import time
from typing import Dict, Optional, Tuple

FRAME_MS = 20
DEFAULT_MIN_FRAMES = 2
DEFAULT_MAX_FRAMES = 12
DEFAULT_TARGET_FRAMES = 4
SILENCE_PCM = b"\x00" * (960 * 2)


class VoiceJitterBuffer:
    """Sequence-ordered buffer with adaptive delay and PLC."""

    def __init__(
        self,
        frame_ms: int = FRAME_MS,
        min_frames: int = DEFAULT_MIN_FRAMES,
        max_frames: int = DEFAULT_MAX_FRAMES,
        target_frames: int = DEFAULT_TARGET_FRAMES,
    ):
        self.frame_ms = max(1, int(frame_ms))
        self.min_frames = max(1, int(min_frames))
        self.max_frames = max(self.min_frames + 1, int(max_frames))
        self.target_frames = max(self.min_frames, min(self.max_frames, int(target_frames)))
        self._frames: Dict[int, bytes] = {}
        self._next_seq: Optional[int] = None
        self._last_pcm = SILENCE_PCM
        self._primed = False
        self._plc_count = 0
        self._late_count = 0
        self._arrival_gap_ms = 20.0
        self._last_arrival = 0.0
        self._playout_frames = self.target_frames
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._frames.clear()
            self._next_seq = None
            self._last_pcm = SILENCE_PCM
            self._primed = False
            self._plc_count = 0
            self._late_count = 0
            self._arrival_gap_ms = 20.0
            self._last_arrival = 0.0
            self._playout_frames = self.target_frames

    @property
    def buffered_ms(self) -> int:
        with self._lock:
            if not self._next_seq:
                return int(len(self._frames) * self.frame_ms)
            ahead = sum(1 for s in self._frames if s >= self._next_seq)
            return int(ahead * self.frame_ms)

    @property
    def plc_frames(self) -> int:
        with self._lock:
            return self._plc_count

    @property
    def playout_delay_ms(self) -> int:
        with self._lock:
            return int(self._playout_frames * self.frame_ms)

    def _update_arrival_stats(self, seq: int) -> None:
        now = time.monotonic()
        if self._last_arrival > 0:
            gap = max(1.0, (now - self._last_arrival) * 1000.0)
            self._arrival_gap_ms = self._arrival_gap_ms * 0.9 + gap * 0.1
            jitter = abs(gap - self.frame_ms)
            want = self.target_frames + int(jitter / self.frame_ms)
            self._playout_frames = max(self.min_frames, min(self.max_frames, want))
        self._last_arrival = now
        if self._next_seq is None:
            self._next_seq = int(seq)

    def push(self, seq: int, pcm_s16: bytes) -> None:
        if not pcm_s16:
            return
        seq = int(seq)
        with self._lock:
            self._update_arrival_stats(seq)
            if self._next_seq is not None and seq < self._next_seq:
                self._late_count += 1
                return
            self._frames[seq] = pcm_s16
            while len(self._frames) > self.max_frames:
                oldest = min(self._frames)
                del self._frames[oldest]
                if self._next_seq is not None and self._next_seq <= oldest:
                    self._next_seq = oldest + 1
            if not self._primed and self._next_seq is not None:
                ahead = sum(1 for s in self._frames if s >= self._next_seq)
                if ahead >= self._playout_frames:
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

    def stats(self) -> dict:
        with self._lock:
            return {
                "buffered_ms": self.buffered_ms,
                "playout_delay_ms": self.playout_delay_ms,
                "plc_frames": self._plc_count,
                "late_frames": self._late_count,
                "primed": self._primed,
                "pending": len(self._frames),
            }