"""Real-time voice/video/screen-share calls over RNS."""

from __future__ import annotations

import json
import threading
import time
import uuid
from enum import Enum
from typing import Callable, Optional

from chatxz.core.media_engine import MediaSession, is_media_packet

MESSAGE_TYPE_CALL = "__call"
MESSAGE_TYPE_MEDIA = "__media"

CALL_INVITE = "invite"
CALL_ACCEPT = "accept"
CALL_REJECT = "reject"
CALL_HANGUP = "hangup"
CALL_UPDATE = "update"
CALL_RINGING = "ringing"
CALL_BUSY = "busy"


class CallState(str, Enum):
    IDLE = "idle"
    OUTGOING = "outgoing"
    INCOMING = "incoming"
    CONNECTING = "connecting"
    ACTIVE = "active"
    ENDED = "ended"


class CallMode(str, Enum):
    AUDIO = "audio"
    VIDEO = "video"
    SCREEN = "screen"


class CallSession:
    def __init__(
        self,
        call_id: str,
        peer_hash: str,
        mode: CallMode,
        outgoing: bool,
        on_state: Optional[Callable] = None,
        on_media_out: Optional[Callable] = None,
        on_media_in: Optional[Callable] = None,
    ):
        self.call_id = call_id
        self.peer_hash = peer_hash
        self.mode = mode
        self.outgoing = outgoing
        self.state = CallState.OUTGOING if outgoing else CallState.INCOMING
        self._on_state = on_state
        self._on_media_out = on_media_out
        self._on_media_in = on_media_in
        self.media = MediaSession()
        self._started_at = time.time()
        self._lock = threading.Lock()
        self.video_enabled = mode in (CallMode.VIDEO, CallMode.SCREEN)
        self.screen_enabled = mode == CallMode.SCREEN
        self.muted = False

    def _emit_state(self):
        if self._on_state:
            try:
                self._on_state(self)
            except Exception as exc:
                print(f"[call] state callback error: {exc}")

    def set_state(self, state: CallState):
        with self._lock:
            self.state = state
        self._emit_state()

    def to_dict(self):
        return {
            "call_id": self.call_id,
            "peer": self.peer_hash,
            "mode": self.mode.value,
            "state": self.state.value,
            "outgoing": self.outgoing,
            "muted": self.muted,
            "video": self.video_enabled,
            "screen": self.screen_enabled,
            "duration_s": int(time.time() - self._started_at) if self.state == CallState.ACTIVE else 0,
        }

    def send_media_packet(self, data: bytes):
        if self._on_media_out:
            self._on_media_out(self.peer_hash, data)

    def handle_media_packet(self, data: bytes):
        parsed = self.media.ingest_packet(data)
        if not parsed:
            return
        kind, flags, seq, ts, payload = parsed
        if self._on_media_in:
            try:
                self._on_media_in(self, kind, flags, seq, ts, payload)
            except Exception as exc:
                print(f"[call] media in callback error: {exc}")

    def hangup(self):
        self.set_state(CallState.ENDED)
        self.media.reset()


class CallManager:
    def __init__(self, send_signaling: Callable, send_media: Callable, get_link_for_peer: Callable):
        self._send_signaling = send_signaling
        self._send_media = send_media
        self._get_link = get_link_for_peer
        self._lock = threading.Lock()
        self._sessions: dict[str, CallSession] = {}
        self._active_call_id: Optional[str] = None
        self._on_event: Optional[Callable] = None

    def set_event_handler(self, handler: Callable):
        self._on_event = handler

    def _emit(self, event: str, data: dict):
        if self._on_event:
            try:
                self._on_event(event, data)
            except Exception as exc:
                print(f"[call] event error: {exc}")

    def _signaling_payload(self, action: str, **kwargs) -> str:
        body = {"action": action, "call_id": kwargs.get("call_id"), **kwargs}
        return json.dumps(body, separators=(",", ":"))

    def active_session(self) -> Optional[CallSession]:
        with self._lock:
            if self._active_call_id:
                return self._sessions.get(self._active_call_id)
        return None

    def start_call(
        self,
        peer_hash: str,
        mode: str = "audio",
        on_media_in: Optional[Callable] = None,
    ) -> Optional[CallSession]:
        with self._lock:
            if self._active_call_id:
                return None
            call_id = str(uuid.uuid4())[:12]
            session = CallSession(
                call_id=call_id,
                peer_hash=peer_hash,
                mode=CallMode(mode),
                outgoing=True,
                on_state=lambda s: self._emit("state", s.to_dict()),
                on_media_out=self._route_media_out,
                on_media_in=on_media_in,
            )
            self._sessions[call_id] = session
            self._active_call_id = call_id
        session.set_state(CallState.OUTGOING)
        self._send_signaling(
            peer_hash,
            self._signaling_payload(
                CALL_INVITE,
                call_id=call_id,
                mode=mode,
            ),
        )
        self._emit("outgoing", session.to_dict())
        return session

    def accept_call(self, call_id: str, on_media_in: Optional[Callable] = None) -> bool:
        with self._lock:
            session = self._sessions.get(call_id)
            if not session or session.state != CallState.INCOMING:
                return False
            if self._active_call_id and self._active_call_id != call_id:
                self._send_signaling(
                    session.peer_hash,
                    self._signaling_payload(CALL_BUSY, call_id=call_id),
                )
                return False
            self._active_call_id = call_id
            if on_media_in:
                session._on_media_in = on_media_in
        session.set_state(CallState.ACTIVE)
        self._send_signaling(
            session.peer_hash,
            self._signaling_payload(CALL_ACCEPT, call_id=call_id, mode=session.mode.value),
        )
        self._emit("accepted", session.to_dict())
        return True

    def reject_call(self, call_id: str):
        with self._lock:
            session = self._sessions.pop(call_id, None)
            if self._active_call_id == call_id:
                self._active_call_id = None
        if session:
            session.set_state(CallState.ENDED)
            self._send_signaling(
                session.peer_hash,
                self._signaling_payload(CALL_REJECT, call_id=call_id),
            )
            self._emit("rejected", {"call_id": call_id})

    def hangup(self, call_id: Optional[str] = None):
        with self._lock:
            cid = call_id or self._active_call_id
            session = self._sessions.pop(cid, None) if cid else None
            if self._active_call_id == cid:
                self._active_call_id = None
        if session:
            session.hangup()
            self._send_signaling(
                session.peer_hash,
                self._signaling_payload(CALL_HANGUP, call_id=session.call_id),
            )
            self._emit("ended", {"call_id": session.call_id})

    def handle_signaling(self, peer_hash: str, content: str):
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return
        action = data.get("action")
        call_id = data.get("call_id")
        if not action or not call_id:
            return

        if action == CALL_INVITE:
            with self._lock:
                if self._active_call_id:
                    self._send_signaling(
                        peer_hash,
                        self._signaling_payload(CALL_BUSY, call_id=call_id),
                    )
                    return
                mode = CallMode(data.get("mode", "audio"))
                session = CallSession(
                    call_id=call_id,
                    peer_hash=peer_hash,
                    mode=mode,
                    outgoing=False,
                    on_state=lambda s: self._emit("state", s.to_dict()),
                    on_media_out=self._route_media_out,
                )
                self._sessions[call_id] = session
                self._active_call_id = call_id
            session.set_state(CallState.INCOMING)
            self._emit("incoming", session.to_dict())
            return

        with self._lock:
            session = self._sessions.get(call_id)

        if not session:
            return

        if action == CALL_ACCEPT:
            session.set_state(CallState.ACTIVE)
            self._emit("accepted", session.to_dict())
        elif action == CALL_REJECT:
            self.hangup(call_id)
        elif action == CALL_HANGUP:
            self.hangup(call_id)
        elif action == CALL_BUSY:
            session.set_state(CallState.ENDED)
            with self._lock:
                if self._active_call_id == call_id:
                    self._active_call_id = None
                self._sessions.pop(call_id, None)
            self._emit("busy", {"call_id": call_id})
        elif action == CALL_UPDATE:
            if "muted" in data:
                session.muted = bool(data["muted"])
            if "video" in data:
                session.video_enabled = bool(data["video"])
            if "screen" in data:
                session.screen_enabled = bool(data["screen"])
            self._emit("update", session.to_dict())

    def handle_media_bytes(self, peer_hash: str, data: bytes):
        if not is_media_packet(data):
            return
        with self._lock:
            session = None
            for s in self._sessions.values():
                if s.peer_hash == peer_hash and s.state == CallState.ACTIVE:
                    session = s
                    break
        if session:
            session.handle_media_packet(data)

    def _route_media_out(self, peer_hash: str, data: bytes):
        self._send_media(peer_hash, data)

    def send_local_media(self, peer_hash: str, packet: bytes):
        session = self.active_session()
        if session and session.peer_hash == peer_hash and session.state == CallState.ACTIVE:
            self._send_media(peer_hash, packet)

    def update_call(self, **kwargs):
        session = self.active_session()
        if not session:
            return
        if "muted" in kwargs:
            session.muted = bool(kwargs["muted"])
        if "video" in kwargs:
            session.video_enabled = bool(kwargs["video"])
        if "screen" in kwargs:
            session.screen_enabled = bool(kwargs["screen"])
        self._send_signaling(
            session.peer_hash,
            self._signaling_payload(
                CALL_UPDATE,
                call_id=session.call_id,
                muted=session.muted,
                video=session.video_enabled,
                screen=session.screen_enabled,
            ),
        )
        self._emit("update", session.to_dict())