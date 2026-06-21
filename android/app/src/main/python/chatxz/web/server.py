import os, json, time, base64, mimetypes, asyncio, socket, zipfile, shutil, subprocess, tempfile, signal, re, sys
from pathlib import Path

from aiohttp import web
import RNS

from chatxz.core.identity import IdentityManager
from chatxz.core.messaging import MessagingBackend
from chatxz.core.voice import VoiceRecorder, VoicePlayer
from chatxz.core.discovery import PeerDiscovery
from chatxz.utils.helpers import get_config_dir, get_data_dir, format_speed
from chatxz.utils.platform import (
    is_android,
    lan_ip as platform_lan_ip,
    lan_broadcast,
    android_storage_dirs,
)
from chatxz.utils.system import get_avg_cpu_temperature, get_cpu_percent

CONFIG_DIR = get_config_dir()
DATA_DIR = get_data_dir()
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")

DEFAULT_RNS_CONFIG = """[reticulum]
enable_transport = Yes
share_instance = No

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
    ifac_size = 16
"""

def build_android_rns_config(broadcast_ip="255.255.255.255"):
    return f"""[reticulum]
enable_transport = No
share_instance = No

[logging]
loglevel = 4

[interfaces]
  [[UDP Interface]]
    type = UDPInterface
    enabled = Yes
    listen_ip = 0.0.0.0
    listen_port = 4242
    forward_ip = {broadcast_ip}
    forward_port = 4242
    ifac_size = 16
"""

def detect_lan_ip():
    if is_android():
        return platform_lan_ip()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None

def cleanup_rns_stale():
    if is_android():
        return
    import glob as _glob
    for p in _glob.glob("/tmp/rns/*/socket"):
        try:
            os.unlink(p)
            print(f"[cleanup] Removed stale RNS socket: {p}")
        except OSError:
            pass
    for p in _glob.glob("/tmp/rns/*"):
        try:
            os.rmdir(p)
        except OSError:
            pass


def _proc_cmdline(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def _is_chatzx_process(pid):
    cmd = _proc_cmdline(pid)
    return "chatxz" in cmd and "grok" not in cmd.lower()


def _port_holder_pids(port, udp=True):
    pids = []
    try:
        flag = "-u" if udp else "-t"
        result = subprocess.run(
            ["ss", "-H", "-n", flag, "-lp"],
            capture_output=True, text=True, timeout=3,
        )
        needle = f":{port}"
        for line in result.stdout.splitlines():
            if needle not in line:
                continue
            for match in re.finditer(r"pid=(\d+)", line):
                pids.append(int(match.group(1)))
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return list(dict.fromkeys(pids))


def _is_port_in_use(port, sock_type=socket.SOCK_DGRAM, host="0.0.0.0"):
    try:
        s = socket.socket(socket.AF_INET, sock_type)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        s.close()
        return False
    except OSError:
        return True


def stop_stale_chatzx_servers(exclude_pid=None):
    """Stop other chatxz server/cli processes holding RNS ports."""
    if is_android():
        return 0
    exclude_pid = exclude_pid or os.getpid()
    targets = set()
    for port in (4242, 8742):
        for pid in _port_holder_pids(port, udp=(port == 4242)):
            if pid != exclude_pid and _is_chatzx_process(pid):
                targets.add(pid)
    try:
        result = subprocess.run(
            ["pgrep", "-f", "chatxz\\.web\\.server|chatxz\\.app|chatxz-web"],
            capture_output=True, text=True, timeout=3,
        )
        for pid_str in result.stdout.split():
            pid = int(pid_str)
            if pid != exclude_pid:
                targets.add(pid)
    except (ValueError, subprocess.TimeoutExpired, OSError):
        pass

    if not targets:
        return 0

    print(f"[startup] Stopping stale chatxz process(es): {', '.join(str(p) for p in sorted(targets))}")
    for pid in sorted(targets):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            print(f"[startup] No permission to stop PID {pid}")

    deadline = time.time() + 5
    while time.time() < deadline:
        if not any(os.path.exists(f"/proc/{p}") for p in targets):
            break
        time.sleep(0.2)

    for pid in sorted(targets):
        if os.path.exists(f"/proc/{pid}"):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    cleanup_rns_stale()
    return len(targets)


def ensure_rns_ports_free(force=False):
    """Free UDP 4242 (RNS) before startup; exit with a clear message if blocked."""
    if is_android():
        return True
    if not _is_port_in_use(4242):
        return True

    holders = _port_holder_pids(4242, udp=True)
    chatxz_holders = [p for p in holders if _is_chatzx_process(p)]

    if chatxz_holders or force:
        stop_stale_chatzx_servers()
        time.sleep(0.5)
        if not _is_port_in_use(4242):
            return True

    holders = _port_holder_pids(4242, udp=True)
    holder_txt = ", ".join(f"PID {p} ({_proc_cmdline(p)[:60]})" for p in holders) or "unknown"
    print(f"[startup] ERROR: UDP port 4242 is already in use by {holder_txt}")
    print("[startup] Another chatxz/RNS instance is probably still running.")
    print("[startup] Stop it with:  pkill -f chatxz.web.server")
    print("[startup] Or restart with:  ./run.sh web --share --force")
    return False

class ChatWebServer:
    def __init__(self, host="127.0.0.1", port=8742, verbose=False, force=False, embedded=False):
        self.host = host
        self.port = port
        self.verbose = verbose
        self.force = force
        self.embedded = embedded
        self.config_dir = CONFIG_DIR
        self.data_dir = DATA_DIR
        os.makedirs(self.config_dir, exist_ok=True)
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(os.path.join(self.config_dir, "received"), exist_ok=True)
        os.makedirs(os.path.join(self.config_dir, "sent"), exist_ok=True)

        self.identity_mgr = IdentityManager(self.config_dir)
        self.identity = None
        self.messaging = None
        self.voice_recorder = None

        self.websockets = set()
        self.message_history = self._load_history()

        self.active_peer = None
        self.discovery = None
        self._loop = None
        self.rns_init_error = None

    @staticmethod
    def _clean_hash(h):
        return (h or "").replace("<", "").replace(">", "").replace(":", "").strip()

    async def _wait_for_rns(self, timeout=90.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.rns_init_error:
                return False, "Network error: " + self.rns_init_error.splitlines()[-1]
            if self.messaging and self.messaging.destination:
                return True, None
            await asyncio.sleep(0.5)
        if self.embedded and not self.rns_init_error:
            return False, "Network stack still starting — wait a few seconds and try again"
        return False, "not ready"

    def _reset_connection_state(self):
        """Clear peer session on server start — UI reconnects explicitly."""
        if self.messaging and self.messaging.active_link:
            try:
                self.messaging.active_link.teardown()
            except Exception:
                pass
            self.messaging.active_link = None
        self.active_peer = None

    def _history_for_peer(self, peer_hash, limit=500):
        peer = self._clean_hash(peer_hash)
        if not peer:
            return self.message_history[-limit:]
        my_hash = self._clean_hash(self.identity_mgr.get_hex_hash())
        filtered = []
        for m in self.message_history:
            mp = self._clean_hash(m.get("peer"))
            if mp:
                if mp == peer:
                    filtered.append(m)
                continue
            sender = self._clean_hash(m.get("sender"))
            if sender in (peer, my_hash, "system"):
                filtered.append(m)
        return filtered[-limit:]

    def load_settings(self):
        try:
            with open(SETTINGS_FILE) as f:
                s = json.load(f)
                s.setdefault("name", "")
                s.setdefault("history_retention", "never")
                s.setdefault("received_dir", os.path.join(self.config_dir, "received"))
                return s
        except:
            return {"name": "", "history_retention": "never",
                    "received_dir": os.path.join(self.config_dir, "received")}

    def save_settings(self, settings):
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=2)

    def _history_file(self):
        return os.path.join(self.config_dir, "history.json")

    def _load_history(self):
        try:
            with open(self._history_file()) as f:
                return json.load(f)
        except:
            return []

    def _save_history(self):
        try:
            with open(self._history_file(), "w") as f:
                json.dump(self.message_history[-1000:], f)
        except:
            pass

    def _apply_retention(self):
        retention = self.load_settings().get("history_retention", "never")
        if retention == "never":
            return
        now = time.time()
        limits = {
            "1d": 86400,
            "1w": 604800,
            "1m": 2592000,
            "6m": 15552000,
            "12m": 31536000,
        }
        seconds = limits.get(retention)
        if seconds:
            self.message_history = [
                m for m in self.message_history
                if now - m.get("timestamp", 0) < seconds
            ]

    def start_rns(self):
        rns_config_path = os.path.join(self.config_dir, "config")
        os.makedirs(self.config_dir, exist_ok=True)
        if is_android():
            bcast = lan_broadcast()
            with open(rns_config_path, "w") as f:
                f.write(build_android_rns_config(bcast))
            print(f"[config] Applied Android RNS config at {rns_config_path} (broadcast={bcast})")
        elif os.path.exists(rns_config_path):
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
                existing += "    ifac_size = 16\n"
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
            elif "share_instance = Yes" in existing:
                existing = existing.replace("share_instance = Yes", "share_instance = No")
                with open(rns_config_path, "w") as f:
                    f.write(existing)
                print(f"[config] Disabled share_instance")
        else:
            template = build_android_rns_config(lan_broadcast()) if is_android() else DEFAULT_RNS_CONFIG
            with open(rns_config_path, "w") as f:
                f.write(template)
            print(f"[config] Created RNS config at {rns_config_path}")

        if not ensure_rns_ports_free(force=self.force):
            msg = "UDP port 4242 is already in use"
            if self.embedded:
                raise RuntimeError(msg)
            sys.exit(1)

        loglevel = RNS.LOG_DEBUG if self.verbose else RNS.LOG_NOTICE
        try:
            RNS.Reticulum(self.config_dir, loglevel=loglevel)
        except OSError as e:
            print(f"[RNS] Bind error: {e}")
            if is_android():
                raise RuntimeError(f"RNS failed to start: {e}") from e
            print("[RNS] Retrying after stopping stale instances...")
            stop_stale_chatzx_servers()
            time.sleep(1)
            if not ensure_rns_ports_free(force=True):
                if self.embedded:
                    raise RuntimeError("UDP port 4242 is already in use")
                sys.exit(1)
            RNS.Reticulum(self.config_dir, loglevel=loglevel)
        except Exception as e:
            if self.embedded:
                raise RuntimeError(f"RNS init failed: {e}") from e
            raise
        self.identity = self.identity_mgr.load_or_create()
        settings = self.load_settings()
        my_ip = detect_lan_ip()
        if my_ip:
            print(f"[network] Detected LAN IP: {my_ip}")
        received_dir = settings.get("received_dir", os.path.join(self.config_dir, "received"))
        self.messaging = MessagingBackend(
            self.identity, self.config_dir,
            on_message=self._on_message,
            on_progress=self._on_transfer_progress,
            display_name=settings.get("name", ""),
            auto_announce=False,
            my_ip=my_ip,
            my_port=self.port,
            receive_dir=received_dir,
        )
        self.voice_recorder = VoiceRecorder(self.config_dir)
        dest = self.messaging.start()

        my_hash = RNS.hexrep(dest.hash)
        self.discovery = PeerDiscovery()
        self.discovery.start()

        return my_hash

    def _on_message(self, chat_msg, sender_hash):
        peer = self._clean_hash(sender_hash) if sender_hash and sender_hash != "system" else self._clean_hash(self.active_peer)
        entry = {
            "type": chat_msg.msg_type,
            "content": chat_msg.content,
            "sender": sender_hash or "system",
            "peer": peer,
            "timestamp": chat_msg.timestamp,
            "file_name": chat_msg.file_name,
            "file_size": chat_msg.file_size,
            "msg_id": chat_msg.msg_id,
            "status": "received" if sender_hash and sender_hash != "system" else "",
        }
        self.message_history.append(entry)
        self._save_history()
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

    def _on_transfer_progress(self, data):
        if self.websockets and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._broadcast({"type": "progress", "data": data}),
                self._loop
            )

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
        if is_android():
            candidates.append(Path(__file__).resolve().parent / "static")
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
        resp = web.FileResponse(filepath)
        if ct:
            resp.headers['Content-Type'] = ct
        return resp

    async def handle_identity(self, request):
        if not self.identity_mgr.identity:
            try:
                self.identity_mgr.load_or_create()
            except Exception:
                pass
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
        link_active = bool(self.messaging and self.messaging.active_link)
        connected = self.active_peer if link_active and self.active_peer else None
        return web.json_response({
            "hash": h,
            "connected": connected,
            "contacts": contacts,
            "discovered": discovered,
            "platform": "android" if is_android() else "desktop",
            "rns_ready": bool(self.messaging and self.messaging.destination),
            "rns_error": self.rns_init_error,
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
            ok = await asyncio.to_thread(self.messaging.connect_to, peer_hash)
            if ok:
                clean = peer_hash.replace("<", "").replace(">", "").replace(":", "").strip()
                self.active_peer = clean
                return web.json_response({"status": "ok"})
            return web.json_response({"error": "connection failed"}, status=400)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_announce(self, request):
        ok, err = await self._wait_for_rns()
        if not ok:
            return web.json_response({"error": err or "not ready"}, status=400)
        try:
            for i in range(3):
                await asyncio.to_thread(self.messaging.announce)
                if i < 2:
                    await asyncio.sleep(0.4)
            bcast = lan_broadcast() if is_android() else None
            return web.json_response({
                "status": "ok",
                "broadcast": bcast,
                "lan_ip": detect_lan_ip(),
            })
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

    def _normalize_received_dir(self, raw):
        path = os.path.normpath(os.path.expanduser((raw or "").strip()))
        if not path:
            return None, "Path is empty"
        if not os.path.isabs(path):
            return None, "Path must be absolute (e.g. /home/user/Downloads)"
        if is_android() and path.startswith("/storage/"):
            try:
                os.makedirs(path, exist_ok=True)
            except OSError as e:
                return None, f"Cannot use folder: {e}"
            if os.path.isdir(path):
                return path, None
            return None, "Path is not a directory"
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as e:
            return None, f"Cannot create directory: {e}"
        if not os.path.isdir(path):
            return None, "Path is not a directory"
        return path, None

    def _apply_received_dir(self, settings):
        received_dir = settings.get("received_dir")
        if not received_dir:
            return
        path, err = self._normalize_received_dir(received_dir)
        if err:
            return
        settings["received_dir"] = path
        if self.messaging:
            self.messaging.receive_dir = path

    def _pick_directory_native(self):
        if is_android():
            return None
        settings = self.load_settings()
        start = settings.get("received_dir", os.path.join(self.config_dir, "received"))
        start = os.path.expanduser(start)
        if not os.path.isdir(start):
            start = os.path.expanduser("~")

        commands = []
        if shutil.which("zenity"):
            commands.append(["zenity", "--file-selection", "--directory", f"--filename={start}/"])
        if shutil.which("kdialog"):
            commands.append(["kdialog", "--getexistingdirectory", start])
        if shutil.which("yad"):
            commands.append(["yad", "--file", "--directory", f"--filename={start}"])

        for cmd in commands:
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                if result.returncode == 0:
                    picked = result.stdout.strip()
                    if picked:
                        return os.path.normpath(picked)
            except Exception:
                continue
        return None

    async def handle_browse_dir(self, request):
        try:
            if request.method == "POST":
                data = await request.json()
                picked = (data.get("path") or "").strip()
                if not picked:
                    return web.json_response({"error": "path required"}, status=400)
                path, err = self._normalize_received_dir(picked)
                if err:
                    return web.json_response({"error": err}, status=400)
                return web.json_response({"path": path})

            if is_android():
                settings = self.load_settings()
                return web.json_response({
                    "platform": "android",
                    "options": android_storage_dirs(),
                    "current": settings.get("received_dir", os.path.join(self.config_dir, "received")),
                })

            picked = await asyncio.to_thread(self._pick_directory_native)
            if not picked:
                return web.json_response({"error": "cancelled"}, status=400)
            path, err = self._normalize_received_dir(picked)
            if err:
                return web.json_response({"error": err}, status=400)
            return web.json_response({"path": path})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_settings_post(self, request):
        try:
            data = await request.json()
            settings = self.load_settings()
            if "name" in data:
                settings["name"] = data["name"].strip()[:50]
            if "history_retention" in data:
                valid = ["1d", "1w", "1m", "6m", "12m", "never", "on_restart", "on_close"]
                if data["history_retention"] in valid:
                    settings["history_retention"] = data["history_retention"]
            if "received_dir" in data:
                path, err = self._normalize_received_dir(data["received_dir"])
                if err:
                    return web.json_response({"error": err}, status=400)
                settings["received_dir"] = path
            self.save_settings(settings)
            if self.messaging:
                self.messaging.display_name = settings.get("name", "")
            self._apply_received_dir(settings)
            self._apply_retention()
            self._save_history()
            return web.json_response({"status": "ok", "settings": settings})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_regenerate_identity(self, request):
        try:
            old_hash = self.identity_mgr.get_hex_hash()
            self.identity = self.identity_mgr.regenerate()
            if self.messaging:
                print("[identity] Restart required for new identity to take full effect")
            return web.json_response({"status": "ok", "old_hash": old_hash, "new_hash": self.identity_mgr.get_hex_hash()})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_restart(self, request):
        if is_android():
            return web.json_response({"error": "Restart is not supported on Android"}, status=400)
        import sys, os
        args = [sys.executable]
        if sys.argv and (sys.argv[0].endswith('.py') or os.sep in sys.argv[0]):
            args.append(sys.argv[0])
        else:
            args += ["-m", "chatxz.web.server"]
        args += sys.argv[1:]
        print(f"[restart] Re-exec'ing: {args}")
        asyncio.get_event_loop().call_later(0.3, lambda: (sys.stdout.flush(), os.execv(sys.executable, args)))
        return web.json_response({"status": "restarting"})

    async def handle_temperature(self, request):
        avg = await asyncio.to_thread(get_avg_cpu_temperature)
        return web.json_response({"avg_celsius": avg})

    async def handle_cpu(self, request):
        pct = await asyncio.to_thread(get_cpu_percent)
        if pct is not None:
            return web.json_response({"cpu_percent": pct})
        return web.json_response({"cpu_percent": None})

    async def handle_debug(self, request):
        peers = self.discovery.get_peers() if self.discovery else []
        settings = self.load_settings()
        received_dir = settings.get("received_dir", os.path.join(self.config_dir, "received"))
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
            "settings": settings,
        })

    async def handle_file_upload(self, request):
        if not self.messaging:
            return web.json_response({"error": "not ready"}, status=400)
        try:
            reader = await request.multipart()
            field = await reader.next()
            if not field:
                return web.json_response({"error": "no file"}, status=400)
            fname = field.filename or f"file_{int(time.time())}"
            ext = os.path.splitext(fname)[1].lower()
            is_image = ext in ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp')

            sent_dir = os.path.join(self.config_dir, "sent")
            save_path = os.path.join(sent_dir, fname)
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            size = 0
            with open(save_path, "wb") as f:
                while True:
                    chunk = await field.read_chunk(8192)
                    if not chunk:
                        break
                    f.write(chunk)
                    size += len(chunk)

            if not self.messaging.active_link:
                self.messaging.enqueue("image" if is_image else "file", save_path,
                                        file_name=fname, file_size=size, file_path=save_path)
                return web.json_response({"status": "queued", "name": fname, "size": size})

            msg_type = "image" if is_image else "file"
            my_hash = self.identity_mgr.get_hex_hash()
            ts = time.time()
            entry = {
                "type": msg_type,
                "content": save_path,
                "sender": my_hash,
                "peer": self._clean_hash(self.active_peer),
                "timestamp": ts,
                "file_name": fname,
                "file_size": size,
                "msg_id": str(int(ts * 1000))[-12:],
                "status": "sent",
            }
            self.message_history.append(entry)
            self._save_history()
            await self._broadcast({"type": "message", "data": entry})

            result = self.messaging.send_file_smart(save_path, msg_type,
                                               progress_callback=self._make_progress_callback(fname, size))
            if result:
                return web.json_response({"status": "ok", "name": fname, "size": size, "method": "resource"})
            return web.json_response({"error": "send failed"}, status=400)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_folder_upload(self, request):
        if not self.messaging:
            return web.json_response({"error": "not ready"}, status=400)
        try:
            folder_name = request.query.get("name", f"folder_{int(time.time())}")
            reader = await request.multipart()
            tmpdir = tempfile.mkdtemp(prefix="chatzx_folder_")
            total_size = 0
            file_count = 0
            while True:
                field = await reader.next()
                if not field:
                    break
                fname = field.filename or f"file_{file_count}"
                fpath = os.path.join(tmpdir, fname)
                os.makedirs(os.path.dirname(fpath), exist_ok=True)
                with open(fpath, "wb") as f:
                    while True:
                        chunk = await field.read_chunk(8192)
                        if not chunk:
                            break
                        f.write(chunk)
                        total_size += len(chunk)
                file_count += 1
            if file_count == 0:
                shutil.rmtree(tmpdir, ignore_errors=True)
                return web.json_response({"error": "no files"}, status=400)
            zip_name = folder_name.rstrip("/") + ".zip"
            sent_dir = os.path.join(self.config_dir, "sent")
            os.makedirs(sent_dir, exist_ok=True)
            zip_path = os.path.join(sent_dir, zip_name)
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in os.walk(tmpdir):
                    for fname in files:
                        fpath = os.path.join(root, fname)
                        arcname = os.path.relpath(fpath, tmpdir)
                        zf.write(fpath, arcname)
            shutil.rmtree(tmpdir, ignore_errors=True)
            zsize = os.path.getsize(zip_path)
            print(f"[folder] Created {zip_name} ({zsize} bytes, {file_count} files)")
            if not self.messaging.active_link:
                self.messaging.enqueue("file", zip_path,
                                        file_name=zip_name, file_size=zsize, file_path=zip_path)
                return web.json_response({"status": "queued", "name": zip_name, "size": zsize})
            my_hash = self.identity_mgr.get_hex_hash()
            ts = time.time()
            entry = {
                "type": "file",
                "content": zip_path,
                "sender": my_hash,
                "peer": self._clean_hash(self.active_peer),
                "timestamp": ts,
                "file_name": zip_name,
                "file_size": zsize,
                "msg_id": str(int(ts * 1000))[-12:],
                "status": "sent",
            }
            self.message_history.append(entry)
            self._save_history()
            await self._broadcast({"type": "message", "data": entry})
            result = self.messaging.send_file_smart(zip_path, "file",
                                               progress_callback=self._make_progress_callback(zip_name, zsize))
            if result:
                return web.json_response({"status": "ok", "name": zip_name, "size": zsize})
            return web.json_response({"error": "send failed"}, status=400)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    def _make_progress_callback(self, fname, total_size):
        start = time.time()
        def callback(resource):
            try:
                progress = resource.get_progress()
                pct = int(progress * 100)
                elapsed = time.time() - start
                bytes_xfer = progress * total_size
                speed = bytes_xfer / elapsed if elapsed > 0 else 0
                speed_str = format_speed(speed)
                self._on_transfer_progress({
                    "file_name": fname,
                    "progress": pct,
                    "size": total_size,
                    "speed": speed_str,
                    "direction": "send",
                    "status": "active",
                })
            except:
                pass
        return callback

    async def handle_transfer_cancel(self, request):
        if not self.messaging:
            return web.json_response({"error": "not ready"}, status=400)
        try:
            data = await request.json() if request.can_read_body else {}
        except Exception:
            data = {}
        transfer_id = data.get("transfer_id")
        cancelled = self.messaging.cancel_transfer(transfer_id)
        await self._broadcast({"type": "progress", "data": {
            "status": "cancelled",
            "progress": 0,
            "file_name": data.get("file_name", ""),
        }})
        return web.json_response({"status": "ok" if cancelled else "noop"})

    async def handle_voice_upload(self, request):
        if not self.messaging:
            return web.json_response({"error": "not ready"}, status=400)
        try:
            data = await request.json()
            audio_b64 = data.get("audio", "")
            if not audio_b64:
                return web.json_response({"error": "no audio data"}, status=400)
            audio_bytes = base64.b64decode(audio_b64)
            sent_dir = os.path.join(self.config_dir, "sent")
            os.makedirs(sent_dir, exist_ok=True)
            voice_path = os.path.join(sent_dir, f"voice_{int(time.time())}.webm")
            with open(voice_path, "wb") as f:
                f.write(audio_bytes)

            if not self.messaging.active_link:
                self.messaging.enqueue("voice", voice_path, file_name=os.path.basename(voice_path),
                                        file_size=len(audio_bytes), file_path=voice_path)
                return web.json_response({"status": "queued"})

            my_hash = self.identity_mgr.get_hex_hash()
            ts = time.time()
            entry = {
                "type": "voice",
                "content": voice_path,
                "sender": my_hash,
                "peer": self._clean_hash(self.active_peer),
                "timestamp": ts,
                "file_name": os.path.basename(voice_path),
                "file_size": len(audio_bytes),
                "msg_id": str(int(ts * 1000))[-12:],
                "status": "sent",
            }
            self.message_history.append(entry)
            self._save_history()
            await self._broadcast({"type": "message", "data": entry})

            result = self.messaging.send_file_smart(voice_path, "voice",
                                               progress_callback=self._make_progress_callback(os.path.basename(voice_path), len(audio_bytes)))
            if result:
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
        settings = self.load_settings()
        received_dir = os.path.normpath(settings.get("received_dir", os.path.join(self.config_dir, "received")))
        sent_dir = os.path.normpath(os.path.join(self.config_dir, "sent"))
        full_path = os.path.normpath(os.path.join(self.config_dir, filepath))

        if not (full_path.startswith(received_dir) or full_path.startswith(sent_dir)):
            return web.Response(text="Forbidden", status=403)
        if not os.path.exists(full_path) or not os.path.isfile(full_path):
            return web.Response(text="Not found: " + full_path, status=404)
        ct, _ = mimetypes.guess_type(full_path)
        if not ct:
            ext = os.path.splitext(full_path)[1].lower()
            ct = {"webm": "audio/webm"}.get(ext.lstrip("."))
        resp = web.FileResponse(full_path)
        if ct:
            resp.headers['Content-Type'] = ct
        return resp

    async def handle_direct_transfer(self, request):
        token = request.match_info.get("token", "")
        if not self.messaging:
            return web.Response(text="Not ready", status=503)
        info = self.messaging.direct_transfer_tokens.get(token)
        if not info:
            return web.Response(text="Invalid or expired token", status=404)
        file_path = info["path"]
        if not os.path.exists(file_path):
            self.messaging.direct_transfer_tokens.pop(token, None)
            return web.Response(text="File not found", status=404)

        fname = info.get("name") or os.path.basename(file_path)
        total = info.get("size") or os.path.getsize(file_path)
        transfer_id = info.get("transfer_id", token)
        ct, _ = mimetypes.guess_type(file_path)
        if not ct:
            ct = "application/octet-stream"

        resp = web.StreamResponse()
        resp.headers["Content-Type"] = ct
        resp.headers["Content-Length"] = str(total)
        resp.headers["X-Direct-Transfer"] = "1"
        await resp.prepare(request)

        sent = 0
        start = time.time()
        try:
            with open(file_path, "rb") as f:
                while True:
                    if self.messaging._cancel_events.get(transfer_id) and self.messaging._cancel_events[transfer_id].is_set():
                        break
                    chunk = f.read(262144)
                    if not chunk:
                        break
                    await resp.write(chunk)
                    sent += len(chunk)
                    elapsed = time.time() - start
                    pct = int(sent * 100 / total) if total else 0
                    speed = format_speed(sent / elapsed) if elapsed > 0 else ""
                    self._on_transfer_progress({
                        "file_name": fname,
                        "progress": pct,
                        "size": total,
                        "speed": speed,
                        "direction": "send",
                        "transfer_id": transfer_id,
                        "status": "active",
                    })
            if sent >= total:
                self._on_transfer_progress({
                    "file_name": fname,
                    "progress": 100,
                    "size": total,
                    "direction": "send",
                    "transfer_id": transfer_id,
                    "status": "complete",
                })
        except Exception as e:
            print(f"[direct] stream error: {e}")
            self._on_transfer_progress({
                "file_name": fname,
                "progress": 0,
                "size": total,
                "direction": "send",
                "transfer_id": transfer_id,
                "status": "failed",
            })
        finally:
            self.messaging.direct_transfer_tokens.pop(token, None)
            await resp.write_eof()
        return resp

    async def handle_queue(self, request):
        if not self.messaging:
            return web.json_response({"count": 0, "items": []})
        return web.json_response({
            "count": self.messaging.queue_size(),
            "items": self.messaging.message_queue[-20:],
        })

    async def handle_queue_clear(self, request):
        if self.messaging:
            self.messaging.message_queue = []
            self.messaging._save_queue()
        return web.json_response({"status": "ok"})

    async def handle_history_clear(self, request):
        self.message_history = []
        self._save_history()
        return web.json_response({"status": "ok"})

    async def handle_delete_message(self, request):
        msg_id = request.match_info.get("msg_id", "")
        if not msg_id:
            return web.json_response({"error": "msg_id required"}, status=400)
        before = len(self.message_history)
        self.message_history = [m for m in self.message_history if m.get("msg_id") != msg_id]
        if len(self.message_history) == before:
            return web.json_response({"error": "not found"}, status=404)
        self._save_history()
        await self._broadcast({"type": "message_deleted", "data": {"msg_id": msg_id}})
        return web.json_response({"status": "ok"})

    async def handle_history(self, request):
        self._apply_retention()
        limit = int(request.query.get("limit", 500))
        peer = request.query.get("peer", "")
        if peer:
            return web.json_response(self._history_for_peer(peer, limit))
        return web.json_response(self.message_history[-limit:])

    async def handle_websocket(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.websockets.add(ws)
        print(f"[ws] Client connected ({len(self.websockets)} total)")

        await self._send_peers_to(ws)

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        await self._handle_ws_message(ws, data)
                    except json.JSONDecodeError:
                        pass
                elif msg.type == web.WSMsgType.ERROR:
                    break
        except:
            pass
        finally:
            self.websockets.discard(ws)
            print(f"[ws] Client disconnected ({len(self.websockets)} total)")
        return ws

    async def _discovery_broadcaster(self):
        print("[broadcaster] Started")
        last_count = -1
        while True:
            await asyncio.sleep(3)
            if not self.websockets or not self.discovery:
                continue
            peers = self.discovery.get_peers()
            count = len(peers)
            if count != last_count:
                print(f"[broadcaster] {count} peer(s), {len(self.websockets)} ws client(s)")
                last_count = count
            if peers:
                await self._broadcast({"type": "peers", "data": peers})

    async def handle_discover(self, request):
        peers = self.discovery.get_peers() if self.discovery else []
        if self.active_peer and not any(p.get("hash", "").replace(":", "") == self.active_peer for p in peers):
            peers.append({
                "hash": self.active_peer,
                "name": self.active_peer[:8],
                "app": "chatxz",
                "connected": True,
            })
        return web.json_response({"peers": peers})

    async def _handle_ws_message(self, ws, data):
        msg_type = data.get("type")
        if msg_type == "send":
            text = data.get("text", "")
            if text and self.messaging:
                if self.messaging.active_link:
                    def on_receipt(status, receipt):
                        if self._loop:
                            asyncio.run_coroutine_threadsafe(
                                self._broadcast({"type": "receipt", "data": {"msg_id": receipt.get("msg_id"), "status": status}}),
                                self._loop
                            )
                    result = self.messaging.send_message(text, receipt_callback=on_receipt)
                    if result:
                        my_hash = self.identity_mgr.get_hex_hash()
                        entry = {
                            "type": result.msg_type,
                            "content": result.content,
                            "sender": my_hash,
                            "peer": self._clean_hash(self.active_peer),
                            "timestamp": result.timestamp,
                            "msg_id": result.msg_id,
                            "status": "sent",
                        }
                        self.message_history.append(entry)
                        self._save_history()
                        await self._broadcast({"type": "message", "data": entry})
                else:
                    self.messaging.enqueue("text", text)
                    qsize = self.messaging.queue_size()
                    await ws.send_str(json.dumps({"type": "info", "data": f"Message queued ({qsize} pending)"}))
        elif msg_type == "connect":
            peer_hash = data.get("hash", "")
            if peer_hash and self.messaging:
                ok = await asyncio.to_thread(self.messaging.connect_to, peer_hash)
                if ok:
                    clean = peer_hash.replace("<", "").replace(">", "").replace(":", "").strip()
                    self.active_peer = clean
                    await ws.send_str(json.dumps({"type": "connect_ok", "hash": clean}))
                else:
                    await ws.send_str(json.dumps({"type": "connect_fail", "error": "connection failed"}))
        elif msg_type == "announce":
            ok, err = await self._wait_for_rns(timeout=30.0)
            if ok:
                for i in range(3):
                    await asyncio.to_thread(self.messaging.announce)
                    if i < 2:
                        await asyncio.sleep(0.4)
            elif err:
                await ws.send_str(json.dumps({"type": "info", "data": "Announce failed: " + err}))
        elif msg_type == "read_receipt":
            msg_id = data.get("msg_id", "")
            if msg_id and self.messaging and self.messaging.active_link:
                self.messaging.send_read_receipt(self.messaging.active_link, msg_id)

    async def _on_startup(self, app):
        self._loop = asyncio.get_running_loop()
        self._reset_connection_state()
        print(f"[startup] Event loop captured: {self._loop}")
        asyncio.create_task(self._discovery_broadcaster())
        retention = self.load_settings().get("history_retention", "never")
        if retention == "on_restart":
            self.message_history = []
            self._save_history()
            print("[history] Cleared on restart")

    def _register_routes(self, app):
        app.router.add_get("/", self.handle_index)
        app.router.add_get("/static/{filename:.*}", self.handle_static)
        app.router.add_get("/api/identity", self.handle_identity)
        app.router.add_post("/api/contacts", self.handle_add_contact)
        app.router.add_delete("/api/contacts/{hash}", self.handle_delete_contact)
        app.router.add_post("/api/connect", self.handle_connect)
        app.router.add_post("/api/announce", self.handle_announce)
        app.router.add_post("/api/disconnect", self.handle_disconnect)
        app.router.add_post("/api/file", self.handle_file_upload)
        app.router.add_post("/api/folder", self.handle_folder_upload)
        app.router.add_post("/api/voice", self.handle_voice_upload)
        app.router.add_post("/api/play", self.handle_play_voice)
        app.router.add_get("/api/history", self.handle_history)
        app.router.add_post("/api/history/clear", self.handle_history_clear)
        app.router.add_delete("/api/history/{msg_id}", self.handle_delete_message)
        app.router.add_get("/api/discover", self.handle_discover)
        app.router.add_get("/api/debug", self.handle_debug)
        app.router.add_get("/api/settings", self.handle_settings_get)
        app.router.add_post("/api/settings", self.handle_settings_post)
        app.router.add_get("/api/browse-dir", self.handle_browse_dir)
        app.router.add_post("/api/browse-dir", self.handle_browse_dir)
        app.router.add_post("/api/transfer/cancel", self.handle_transfer_cancel)
        app.router.add_get("/api/file/{filepath:.*}", self.handle_serve_file)
        app.router.add_get("/api/direct-transfer/{token}", self.handle_direct_transfer)
        app.router.add_get("/api/queue", self.handle_queue)
        app.router.add_delete("/api/queue", self.handle_queue_clear)
        app.router.add_post("/api/identity/regenerate", self.handle_regenerate_identity)
        app.router.add_post("/api/restart", self.handle_restart)
        app.router.add_get("/api/temperature", self.handle_temperature)
        app.router.add_get("/api/cpu", self.handle_cpu)
        app.router.add_get("/api/health", self.handle_health)
        app.router.add_get("/ws", self.handle_websocket)

    async def handle_health(self, request):
        status = "ok" if not self.rns_init_error else "rns_error"
        return web.json_response({
            "status": status,
            "rns_ready": self.messaging is not None,
            "rns_error": self.rns_init_error,
        })

    async def _embedded_init_rns(self, app):
        """Start Reticulum after the HTTP server is already listening."""
        try:
            my_hash = await asyncio.to_thread(self.start_rns)
            print(f"[embedded] RNS ready, identity: {my_hash}")
        except Exception:
            import traceback
            self.rns_init_error = traceback.format_exc()
            print(f"[embedded] RNS init failed:\n{self.rns_init_error}")

    def run_embedded(self):
        """Blocking server loop for embedded hosts (Android/Chaquopy)."""
        app = web.Application()
        self._register_routes(app)

        async def _embedded_startup(app):
            self._loop = asyncio.get_running_loop()
            self._reset_connection_state()
            asyncio.create_task(self._discovery_broadcaster())
            asyncio.create_task(self._embedded_init_rns(app))
            retention = self.load_settings().get("history_retention", "never")
            if retention == "on_restart":
                self.message_history = []
                self._save_history()

        app.on_startup.append(_embedded_startup)
        print(f"[embedded] starting http://{self.host}:{self.port}")

        async def _serve():
            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            site = web.TCPSite(runner, self.host, self.port, reuse_address=True)
            await site.start()
            while True:
                await asyncio.sleep(3600)

        asyncio.run(_serve())

    def run(self):
        app = web.Application()
        self._register_routes(app)
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
    parser.add_argument("--force", "-f", action="store_true",
                        help="Stop any existing chatxz server before starting")
    args = parser.parse_args()
    host = "0.0.0.0" if args.share else args.host
    server = ChatWebServer(host=host, port=args.port, verbose=args.verbose, force=args.force)
    server.run()


if __name__ == "__main__":
    main()
