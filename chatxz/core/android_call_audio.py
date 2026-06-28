"""Android native call audio bridge (Java CallAudioEngine ↔ Python server)."""

from __future__ import annotations

from typing import Callable, Optional

_send_fn: Optional[Callable[[str, str], bool]] = None
_play_fn: Optional[Callable[[int, str], None]] = None


def register_handlers(
    send_fn: Callable[[str, str], bool],
    play_fn: Callable[[int, str], None],
) -> None:
    global _send_fn, _play_fn
    _send_fn = send_fn
    _play_fn = play_fn


def clear_handlers() -> None:
    global _send_fn, _play_fn
    _send_fn = None
    _play_fn = None


def on_encoded_opus(b64: str) -> bool:
    """Called from Java when MediaCodec produces an Opus packet."""
    if not b64 or not _send_fn:
        return False
    from chatxz.core.opus_native import OPUS_CODEC
    return bool(_send_fn(b64, OPUS_CODEC))


def play_incoming_opus(seq: int, b64: str) -> None:
    """Forward received Opus to Java playback engine."""
    if not b64 or not _play_fn:
        return
    _play_fn(int(seq or 0), b64)


def is_active() -> bool:
    return _send_fn is not None