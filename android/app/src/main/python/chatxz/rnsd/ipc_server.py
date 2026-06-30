"""Headless IPC transport for the Rust chatxz application."""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from typing import Any, Callable, Awaitable

from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from multidict import CIMultiDict

EventSink = Callable[[dict], Awaitable[None]]


class IpcServer:
    def __init__(self, backend, host: str = "127.0.0.1", port: int = 8744):
        self.backend = backend
        self.host = host
        self.port = port
        self._clients: set[asyncio.StreamWriter] = set()
        self._server: asyncio.Server | None = None

    async def start(self):
        self._server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )
        print(f"[rnsd] IPC listening on {self.host}:{self.port}")

    async def stop(self):
        for writer in list(self._clients):
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        self._clients.clear()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def push_event(self, payload: dict):
        if not self._clients:
            return
        line = json.dumps({"op": "event", "payload": payload}, separators=(",", ":"))
        dead = []
        for writer in self._clients:
            try:
                writer.write((line + "\n").encode("utf-8"))
                await writer.drain()
            except Exception:
                dead.append(writer)
        for writer in dead:
            self._clients.discard(writer)

    def bind_event_sink(self):
        self.backend.set_event_sink(self.push_event)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        self._clients.add(writer)
        try:
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                try:
                    msg = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                op = msg.get("op")
                if op == "http":
                    resp = await self._dispatch_http(msg)
                    await self._send(writer, resp)
                elif op == "ws":
                    await self.backend.dispatch_ws(msg.get("data") or {})
                elif op == "rns":
                    resp = await self.backend.dispatch_rns(
                        msg.get("method") or "", msg.get("params") or {}
                    )
                    await self._send(
                        writer,
                        {"op": "rns_response", "id": msg.get("id"), "result": resp},
                    )
                elif op == "ping":
                    await self._send(writer, {"op": "pong"})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[rnsd] IPC client error: {exc}", file=sys.stderr)
        finally:
            self._clients.discard(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _send(self, writer: asyncio.StreamWriter, payload: dict):
        line = json.dumps(payload, separators=(",", ":"))
        writer.write((line + "\n").encode("utf-8"))
        await writer.drain()

    async def _dispatch_http(self, msg: dict) -> dict:
        req_id = msg.get("id")
        method = (msg.get("method") or "GET").upper()
        path = msg.get("path") or "/"
        query = msg.get("query") or {}
        headers = CIMultiDict(msg.get("headers") or {})
        body_b64 = msg.get("body_b64")
        body = base64.b64decode(body_b64) if body_b64 else b""

        if query:
            from urllib.parse import urlencode

            qs = urlencode(query, doseq=True)
            path = f"{path}?{qs}" if "?" not in path else f"{path}&{qs}"

        try:
            bare_path = path.split("?", 1)[0]
            request = make_mocked_request(method, path, headers=headers, payload=body)
            handler, match_info = self.backend.route_for(method, bare_path)
            if match_info:
                request.match_info = match_info
            if not handler:
                return {
                    "op": "http_response",
                    "id": req_id,
                    "status": 404,
                    "content_type": "application/json",
                    "body": {"error": "not_found"},
                }
            response = await handler(request)
            return self._encode_response(req_id, response)
        except web.HTTPException as exc:
            return {
                "op": "http_response",
                "id": req_id,
                "status": exc.status,
                "content_type": "application/json",
                "body": {"error": exc.reason or str(exc.status)},
            }
        except Exception as exc:
            return {
                "op": "http_response",
                "id": req_id,
                "status": 500,
                "content_type": "application/json",
                "body": {"error": str(exc)},
            }

    def _response_bytes(self, response: web.Response) -> bytes:
        from aiohttp import web_fileresponse

        if isinstance(response, web_fileresponse.FileResponse):
            path = getattr(response, "path", None) or getattr(response, "_path", None)
            if path:
                with open(path, "rb") as fh:
                    return fh.read()
            return b""
        text = getattr(response, "text", None)
        if text is not None:
            return text.encode("utf-8") if isinstance(text, str) else bytes(text)
        body = getattr(response, "_body", None) or getattr(response, "body", None)
        if body is not None:
            return body if isinstance(body, bytes) else str(body).encode("utf-8")
        return b""

    def _encode_response(self, req_id: Any, response: web.Response) -> dict:
        content_type = response.content_type or "application/octet-stream"
        raw = self._response_bytes(response)
        if content_type.startswith("application/json") and raw:
            try:
                body = json.loads(raw.decode("utf-8"))
                return {
                    "op": "http_response",
                    "id": req_id,
                    "status": response.status,
                    "content_type": content_type,
                    "body": body,
                }
            except json.JSONDecodeError:
                pass
        if not raw:
            return {
                "op": "http_response",
                "id": req_id,
                "status": response.status,
                "content_type": content_type,
                "body": None,
            }
        return {
            "op": "http_response",
            "id": req_id,
            "status": response.status,
            "content_type": content_type,
            "body_b64": base64.b64encode(raw).decode("ascii"),
        }