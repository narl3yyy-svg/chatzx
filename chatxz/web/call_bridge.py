"""Bridge between CallManager, RNS media transport, and web clients."""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Optional

from chatxz.core.calls import CallManager, CallState
from chatxz.core.media_engine import MediaSession, parse_packet, rust_available

if TYPE_CHECKING:
    from chatxz.web.server import ChatWebServer


class CallBridge:
    def __init__(self, server: "ChatWebServer"):
        self.server = server
        self.manager: Optional[CallManager] = None
        self._local_sessions: dict[str, MediaSession] = {}
        self._media_ws = set()

    def attach(self):
        self.manager = CallManager(
            send_signaling=self._send_signaling,
            send_media=self._send_media,
            get_link_for_peer=lambda h: True,
        )
        self.manager.set_event_handler(self._on_call_event)

    def _send_signaling(self, peer_hash: str, payload: str):
        if self.server.messaging:
            self.server.messaging.send_call_signaling(payload, target_peer=peer_hash)

    def _send_media(self, peer_hash: str, data: bytes):
        if self.server.messaging:
            self.server.messaging.send_media_packet(data, target_peer=peer_hash)

    def on_signaling(self, peer_hash: str, content: str):
        if self.manager:
            self.manager.handle_signaling(peer_hash, content)

    def on_media_packet(self, peer_hash: str, data: bytes):
        if self.manager:
            self.manager.handle_media_bytes(peer_hash, data)
        parsed = parse_packet(data)
        if parsed and self.server._loop:
            kind, flags, seq, ts, payload = parsed
            if kind == 1:
                session = self._session_for_peer(peer_hash)
                try:
                    session.ingest_packet(data)
                    result = session.pop_audio(ts)
                    if result:
                        _, pcm = result
                        payload = pcm
                except Exception:
                    pass
            asyncio.run_coroutine_threadsafe(
                self._broadcast_media(peer_hash, kind, flags, seq, ts, payload),
                self.server._loop,
            )

    async def _broadcast_media(self, peer_hash, kind, flags, seq, ts, payload):
        msg = json.dumps({
            "type": "media",
            "peer": peer_hash,
            "kind": kind,
            "flags": flags,
            "seq": seq,
            "ts": ts,
            "data": payload.hex() if isinstance(payload, (bytes, bytearray)) else "",
        })
        dead = []
        for ws in list(self._media_ws):
            try:
                await ws.send_str(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._media_ws.discard(ws)

    def _on_call_event(self, event: str, data: dict):
        if self.server._loop:
            asyncio.run_coroutine_threadsafe(
                self.server._broadcast({"type": "call", "event": event, "data": data}),
                self.server._loop,
            )

    def _session_for_peer(self, peer_hash: str) -> MediaSession:
        key = peer_hash or "default"
        if key not in self._local_sessions:
            self._local_sessions[key] = MediaSession()
        return self._local_sessions[key]

    async def handle_api(self, request, action: str):
        if not self.manager:
            return {"error": "calls not ready"}
        body = {}
        if request.method == "POST":
            try:
                body = await request.json()
            except Exception:
                body = {}
        peer = (body.get("peer") or "").strip()
        if not peer and action not in ("status",):
            return {"error": "peer required"}
        peer = self.server._peer_dest_hash(peer) or peer

        if action == "start":
            mode = body.get("mode", "audio")
            session = self.manager.start_call(peer, mode=mode)
            if not session:
                return {"error": "busy"}
            return {"status": "ok", "call": session.to_dict()}

        if action == "accept":
            ok = self.manager.accept_call(body.get("call_id", ""))
            return {"status": "ok" if ok else "error"}

        if action == "reject":
            self.manager.reject_call(body.get("call_id", ""))
            return {"status": "ok"}

        if action == "hangup":
            self.manager.hangup(body.get("call_id"))
            return {"status": "ok"}

        if action == "update":
            self.manager.update_call(
                muted=body.get("muted"),
                video=body.get("video"),
                screen=body.get("screen"),
            )
            s = self.manager.active_session()
            return {"status": "ok", "call": s.to_dict() if s else None}

        if action == "status":
            s = self.manager.active_session()
            return {
                "status": "ok",
                "call": s.to_dict() if s else None,
                "rust_media": rust_available(),
            }
        return {"error": "unknown action"}

    async def handle_media_ws(self, request):
        ws = __import__("aiohttp").web.WebSocketResponse(heartbeat=15.0)
        await ws.prepare(request)
        self._media_ws.add(ws)
        peer_filter = request.query.get("peer", "").strip()
        try:
            async for msg in ws:
                if msg.type != __import__("aiohttp").web.WSMsgType.BINARY:
                    if msg.type == __import__("aiohttp").web.WSMsgType.TEXT:
                        try:
                            data = json.loads(msg.data)
                            if data.get("type") == "ping":
                                await ws.send_str(json.dumps({"type": "pong"}))
                        except Exception:
                            pass
                    continue
                if not self.manager or not self.server.messaging:
                    continue
                active = self.manager.active_session()
                if not active or active.state != CallState.ACTIVE:
                    continue
                peer = active.peer_hash
                if peer_filter and self.server._peer_dest_hash(peer_filter) != peer:
                    continue
                raw = msg.data
                if len(raw) < 4:
                    continue
                frame_type = raw[0]
                ts = int.from_bytes(raw[1:5], "big")
                payload = raw[5:]
                session = self._session_for_peer(peer)
                if frame_type == 1:
                    try:
                        opus = session.encode_audio_frame(payload)
                        pkt = session.packetize_audio(opus, ts)
                        self._send_media(peer, pkt)
                    except Exception as exc:
                        print(f"[call] audio encode error: {exc}")
                elif frame_type == 2:
                    keyframe = raw[5] == 1 if len(raw) > 5 else False
                    vid_data = raw[6:] if len(raw) > 6 else b""
                    pkt = session.packetize_video(vid_data, ts, keyframe=keyframe)
                    self._send_media(peer, pkt)
                elif frame_type == 3:
                    keyframe = raw[5] == 1 if len(raw) > 5 else False
                    scr_data = raw[6:] if len(raw) > 6 else b""
                    pkt = session.packetize_screen(scr_data, ts, keyframe=keyframe)
                    self._send_media(peer, pkt)
        finally:
            self._media_ws.discard(ws)
        return ws