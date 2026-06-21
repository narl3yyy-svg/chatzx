import os
import json
import time
import base64
import tempfile
import mimetypes
import asyncio
from pathlib import Path

import aiohttp
from aiohttp import web
import RNS

from chatxz.core.identity import IdentityManager
from chatxz.core.messaging import MessagingBackend, ChatMessage
from chatxz.core.filetransfer import FileTransfer
from chatxz.core.voice import VoiceRecorder, VoicePlayer
from chatxz.core.discovery import PeerDiscovery
from chatxz.utils.helpers import get_config_dir, get_data_dir, format_size, truncate_hash

CONFIG_DIR = get_config_dir()
DATA_DIR = get_data_dir()
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")

DEFAULT_RNS_CONFIG = """[reticulum]
enable_transport = Yes
share_instance = Yes

[logging]
loglevel = 3

[interfaces]
  [[Default Interface]]
    type = AutoInterface
    enabled = Yes

  [[UDP Interface]]
    type = UDPInterface
    enabled = Yes
    listen_ip = 0.0.0.0
    listen_port = 4242
    forward_ip = 255.255.255.255
    forward_port = 4242
    ifac_size = 8
"""

class ChatWebServer:
    def __init__(self, host="127.0.0.1", port=8742, verbose=False):
        self.host = host
        self.port = port
        self.verbose = verbose
        self.config_dir = CONFIG_DIR
        self.data_dir = DATA_DIR
        os.makedirs(self.config_dir, exist_ok=True)
        os.makedirs(self.data_dir, exist_ok=True)

        self.identity_mgr = IdentityManager(self.config_dir)
        self.identity = None
        self.messaging = None
        self.file_transfer = None
        self.voice_recorder = None

        self.websockets = set()
        self.message_history = []
        self.contact_list = {}
        self.active_peer = None
        self.discovery = None
        self._loop = None

    def load_settings(self):
        try:
            with open(SETTINGS_FILE) as f:
                return json.load(f)
        except:
            return {"name": "", "announce_interval": 30}

    def save_settings(self, settings):
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=2)

    def start_rns(self):
        rns_config_path = os.path.join(self.config_dir, "config")
        os.makedirs(self.config_dir, exist_ok=True)
        if os.path.exists(rns_config_path):
            with open(rns_config_path) as f:
                existing = f.read()
            modified = False
            if "enable_transport = False" in existing:
                existing = existing.replace("enable_transport = False", "enable_transport = Yes")
                modified = True
            if "AutoInterface" not in existing:
                existing += "\n\n  [[Default Interface]]\n"
                existing += "    type = AutoInterface\n"
                existing += "    enabled = Yes\n"
                modified = True
            if "UDPInterface" not in existing:
                existing += "\n  [[UDP Interface]]\n"
                existing += "    type = UDPInterface\n"
                existing += "    enabled = Yes\n"
                existing += "    listen_ip = 0.0.0.0\n"
                existing += "    listen_port = 4242\n"
                existing += "    forward_ip = 255.255.255.255\n"
                existing += "    forward_port = 4242\n"
                existing += "    ifac_size = 8\n"
                modified = True
            elif "enabled = Yes" not in existing:
                existing = existing.replace(
                    "type = UDPInterface",
                    "type = UDPInterface\n    enabled = Yes"
                )
                modified = True
            if modified:
                with open(rns_config_path, "w") as f:
                    f.write(existing)
                print(f"[config] Updated {rns_config_path}")
        else:
            with open(rns_config_path, "w") as f:
                f.write(DEFAULT_RNS_CONFIG)
            print(f"[config] Created RNS config at {rns_config_path}")

        loglevel = RNS.LOG_DEBUG if self.verbose else RNS.LOG_NOTICE
        RNS.Reticulum(self.config_dir, loglevel=loglevel)
        self.identity = self.identity_mgr.load_or_create()
        settings = self.load_settings()
        self.messaging = MessagingBackend(
            self.identity, self.config_dir,
            on_message=self._on_message,
            display_name=settings.get("name", ""),
            announce_interval=settings.get("announce_interval", 30),
            auto_announce=False,
        )
        self.file_transfer = FileTransfer(self.config_dir)
        self.voice_recorder = VoiceRecorder(self.config_dir)
        dest = self.messaging.start()

        my_hash = RNS.hexrep(dest.hash)
        self.discovery = PeerDiscovery()
        self.discovery.start()

        return my_hash

    def _on_message(self, chat_msg, sender_hash):
        entry = {
            "type": chat_msg.msg_type,
            "content": chat_msg.content,
            "sender": sender_hash or "system",
            "timestamp": chat_msg.timestamp,
            "file_name": chat_msg.file_name,
            "file_size": chat_msg.file_size,
        }
        self.message_history.append(entry)
        if self.websockets and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._broadcast({"type": "message", "data": entry}),
                self._loop
            )

    async def _broadcast(self, data):
        msg = json.dumps(data)
        for ws in self.websockets.copy():
            try:
                await ws.send_str(msg)
            except:
                self.websockets.discard(ws)

    async def _send_peers_to(self, ws):
        if self.discovery:
            peers = self.discovery.get_peers()
            try:
                await ws.send_str(json.dumps({"type": "peers", "data": peers}))
            except:
                pass

    def _static_dir(self):
        candidates = [
            Path(__file__).parent / "static",
            Path.cwd() / "chatxz" / "web" / "static",
            Path.cwd() / "static",
        ]
        for p in candidates:
            if p.exists() and (p / "index.html").exists():
                return p
        return candidates[0]

    async def handle_index(self, request):
        static_dir = self._static_dir()
        index_path = static_dir / "index.html"
        if not index_path.exists():
            return web.Response(text="Frontend not found", status=500)
        return web.FileResponse(index_path)

    async def handle_static(self, request):
        static_dir = self._static_dir()
        filepath = static_dir / request.match_info["filename"]
        if not filepath.exists() or not filepath.is_file():
            return web.Response(text="Not found", status=404)
        ct, _ = mimetypes.guess_type(str(filepath))
        return web.FileResponse(filepath, content_type=ct or "application/octet-stream")

    async def handle_identity(self, request):
        h = self.identity_mgr.get_hex_hash()
        contacts = []
        contacts_dir = os.path.join(self.config_dir, "contacts")
        os.makedirs(contacts_dir, exist_ok=True)
        for f in os.listdir(contacts_dir):
            path = os.path.join(contacts_dir, f)
            try:
                with open(path) as fh:
                    name = fh.read().strip()
                contacts.append({"hash": f, "name": name})
            except:
                contacts.append({"hash": f, "name": f})
        discovered = self.discovery.get_peers() if self.discovery else []
        return web.json_response({
            "hash": h,
            "connected": self.active_peer,
            "contacts": contacts,
            "discovered": discovered,
        })

    async def handle_add_contact(self, request):
        try:
            data = await request.json()
            peer_hash = data.get("hash", "").strip().replace(":", "")
            name = data.get("name", peer_hash).strip()
            if not peer_hash:
                return web.json_response({"error": "hash required"}, status=400)
            contacts_dir = os.path.join(self.config_dir, "contacts")
            os.makedirs(contacts_dir, exist_ok=True)
            path = os.path.join(contacts_dir, peer_hash)
            with open(path, "w") as f:
                f.write(name)
            return web.json_response({"status": "ok"})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_delete_contact(self, request):
        try:
            peer_hash = request.match_info["hash"].replace(":", "")
            contacts_dir = os.path.join(self.config_dir, "contacts")
            path = os.path.join(contacts_dir, peer_hash)
            if os.path.exists(path):
                os.unlink(path)
                return web.json_response({"status": "ok"})
            return web.json_response({"error": "not found"}, status=404)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_connect(self, request):
        try:
            data = await request.json()
            peer_hash = data.get("hash", "").strip()
            if not peer_hash:
                return web.json_response({"error": "hash required"}, status=400)
            ok = self.messaging.connect_to(peer_hash)
            if ok:
                clean = peer_hash.replace("<", "").replace(">", "").replace(":", "").strip()
                self.active_peer = clean
                return web.json_response({"status": "ok"})
            return web.json_response({"error": "connection failed"}, status=400)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_disconnect(self, request):
        if self.messaging and self.messaging.active_link:
            try:
                self.messaging.active_link.teardown()
            except:
                pass
            self.messaging.active_link = None
        self.active_peer = None
        return web.json_response({"status": "ok"})

    async def handle_settings_get(self, request):
        return web.json_response(self.load_settings())

    async def handle_settings_post(self, request):
        try:
            data = await request.json()
            settings = self.load_settings()
            if "name" in data:
                settings["name"] = data["name"].strip()[:50]
            if "announce_interval" in data:
                val = int(data["announce_interval"])
                settings["announce_interval"] = max(5, min(3600, val))
            self.save_settings(settings)
            if self.messaging:
                self.messaging.display_name = settings.get("name", "")
                self.messaging.announce_interval = settings.get("announce_interval", 30)
            return web.json_response({"status": "ok", "settings": settings})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_debug(self, request):
        peers = self.discovery.get_peers() if self.discovery else []
        received_dir = os.path.join(self.config_dir, "received")
        return web.json_response({
            "identity_hash": self.identity_mgr.get_hex_hash() if self.identity_mgr else None,
            "ws_clients": len(self.websockets),
            "discovered_peers": peers,
            "discovery_running": self.discovery.running if self.discovery else False,
            "active_peer": self.active_peer,
            "message_count": len(self.message_history),
            "loop_running": self._loop is not None and self._loop.is_running(),
            "rns_interfaces": len(RNS.Transport.interfaces) if hasattr(RNS.Transport, 'interfaces') else "unknown",
            "received_files_dir": received_dir,
        })

    async def handle_file_upload(self, request):
        if not self.messaging or not self.messaging.active_link:
            return web.json_response({"error": "not connected"}, status=400)
        try:
            reader = await request.multipart()
            field = await reader.next()
            if not field:
                return web.json_response({"error": "no file"}, status=400)
            fname = field.filename or f"file_{int(time.time())}"
            ext = os.path.splitext(fname)[1].lower()
            is_image = ext in ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp')

            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
            size = 0
            while True:
                chunk = await field.read_chunk(8192)
                if not chunk:
                    break
                tmp.write(chunk)
                size += len(chunk)
            tmp.close()

            msg_type = "image" if is_image else "file"
            result = self.messaging.send_file(tmp.name, msg_type)
            os.unlink(tmp.name)
            if result:
                my_hash = self.identity_mgr.get_hex_hash()
                entry = {
                    "type": result.msg_type,
                    "content": result.content,
                    "sender": my_hash,
                    "timestamp": result.timestamp,
                    "file_name": result.file_name,
                    "file_size": result.file_size,
                }
                self.message_history.append(entry)
                await self._broadcast({"type": "message", "data": entry})
                return web.json_response({"status": "ok", "name": fname, "size": size})
            return web.json_response({"error": "send failed"}, status=400)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_voice_upload(self, request):
        if not self.messaging or not self.messaging.active_link:
            return web.json_response({"error": "not connected"}, status=400)
        try:
            data = await request.json()
            audio_b64 = data.get("audio", "")
            if not audio_b64:
                return web.json_response({"error": "no audio data"}, status=400)
            audio_bytes = base64.b64decode(audio_b64)
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".webm")
            tmp.write(audio_bytes)
            tmp.close()
            result = self.messaging.send_file(tmp.name, "voice")
            os.unlink(tmp.name)
            if result:
                my_hash = self.identity_mgr.get_hex_hash()
                entry = {
                    "type": result.msg_type,
                    "content": result.content,
                    "sender": my_hash,
                    "timestamp": result.timestamp,
                    "file_name": result.file_name,
                    "file_size": result.file_size,
                }
                self.message_history.append(entry)
                await self._broadcast({"type": "message", "data": entry})
                return web.json_response({"status": "ok"})
            return web.json_response({"error": "send failed"}, status=400)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_play_voice(self, request):
        try:
            data = await request.json()
            path = data.get("path", "")
            if os.path.exists(path):
                VoicePlayer.play(path)
                return web.json_response({"status": "ok"})
            return web.json_response({"error": "file not found"}, status=404)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_serve_file(self, request):
        filepath = request.match_info["filepath"]
        received_dir = os.path.join(self.config_dir, "received")
        full_path = os.path.normpath(os.path.join(received_dir, filepath))
        if not full_path.startswith(os.path.normpath(received_dir)):
            return web.Response(text="Forbidden", status=403)
        if not os.path.exists(full_path) or not os.path.isfile(full_path):
            return web.Response(text="Not found", status=404)
        ct, _ = mimetypes.guess_type(full_path)
        return web.FileResponse(full_path, content_type=ct or "application/octet-stream")

    async def handle_history(self, request):
        limit = int(request.query.get("limit", 100))
        return web.json_response(self.message_history[-limit:])

    async def handle_websocket(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.websockets.add(ws)
        print(f"[ws] Client connected ({len(self.websockets)} total)")

        await self._send_peers_to(ws)

        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        await self._handle_ws_message(ws, data)
                    except json.JSONDecodeError:
                        pass
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    break
        except:
            pass
        finally:
            self.websockets.discard(ws)
            print(f"[ws] Client disconnected ({len(self.websockets)} total)")
        return ws

    async def _discovery_broadcaster(self):
        print("[broadcaster] Started")
        while True:
            await asyncio.sleep(3)
            if self.discovery:
                peers = self.discovery.get_peers()
                if peers:
                    print(f"[broadcaster] Broadcasting {len(peers)} peers to {len(self.websockets)} ws clients")
                    await self._broadcast({"type": "peers", "data": peers})

    async def handle_discover(self, request):
        if self.discovery:
            peers = self.discovery.get_peers()
            return web.json_response({"peers": peers})
        return web.json_response({"peers": []})

    async def _handle_ws_message(self, ws, data):
        msg_type = data.get("type")
        if msg_type == "send":
            text = data.get("text", "")
            if text and self.messaging:
                result = self.messaging.send_message(text)
                if result:
                    my_hash = self.identity_mgr.get_hex_hash()
                    entry = {
                        "type": result.msg_type,
                        "content": result.content,
                        "sender": my_hash,
                        "timestamp": result.timestamp,
                    }
                    self.message_history.append(entry)
                    await self._broadcast({"type": "message", "data": entry})
        elif msg_type == "connect":
            peer_hash = data.get("hash", "")
            if peer_hash and self.messaging:
                ok = self.messaging.connect_to(peer_hash)
                if ok:
                    clean = peer_hash.replace("<", "").replace(">", "").replace(":", "").strip()
                    self.active_peer = clean
                    await ws.send_str(json.dumps({"type": "connect_ok", "hash": clean}))
                else:
                    await ws.send_str(json.dumps({"type": "connect_fail", "error": "connection failed"}))
        elif msg_type == "announce":
            if self.messaging and self.messaging.destination:
                self.messaging.announce()

    async def _on_startup(self, app):
        self._loop = asyncio.get_running_loop()
        print(f"[startup] Event loop captured: {self._loop}")
        asyncio.create_task(self._discovery_broadcaster())

    def run(self):
        app = web.Application()

        app.router.add_get("/", self.handle_index)
        app.router.add_get("/static/{filename:.*}", self.handle_static)
        app.router.add_get("/api/identity", self.handle_identity)
        app.router.add_post("/api/contacts", self.handle_add_contact)
        app.router.add_delete("/api/contacts/{hash}", self.handle_delete_contact)
        app.router.add_post("/api/connect", self.handle_connect)
        app.router.add_post("/api/disconnect", self.handle_disconnect)
        app.router.add_post("/api/file", self.handle_file_upload)
        app.router.add_post("/api/voice", self.handle_voice_upload)
        app.router.add_post("/api/play", self.handle_play_voice)
        app.router.add_get("/api/history", self.handle_history)
        app.router.add_get("/api/discover", self.handle_discover)
        app.router.add_get("/api/debug", self.handle_debug)
        app.router.add_get("/api/settings", self.handle_settings_get)
        app.router.add_post("/api/settings", self.handle_settings_post)
        app.router.add_get("/api/file/{filepath:.*}", self.handle_serve_file)
        app.router.add_get("/ws", self.handle_websocket)

        my_hash = self.start_rns()

        app.on_startup.append(self._on_startup)

        print(f"chatxz web server v0.1.0")
        print(f"Your identity: {my_hash}")
        print(f"Web interface: http://{self.host}:{self.port}")
        print("Press Ctrl+C to stop")

        web.run_app(app, host=self.host, port=self.port, print=lambda _: None)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="chatxz web server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address")
    parser.add_argument("--port", type=int, default=8742, help="Port")
    parser.add_argument("--share", action="store_true", help="Listen on 0.0.0.0 (accessible on LAN)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show RNS debug logs")
    args = parser.parse_args()
    host = "0.0.0.0" if args.share else args.host
    server = ChatWebServer(host=host, port=args.port, verbose=args.verbose)
    server.run()


if __name__ == "__main__":
    main()
