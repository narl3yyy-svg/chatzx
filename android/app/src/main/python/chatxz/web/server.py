import os, json, time, base64, mimetypes, asyncio, socket, zipfile, shutil, subprocess, tempfile, signal, re, sys, threading, uuid
from urllib.parse import quote, unquote
from pathlib import Path

from aiohttp import web
import RNS

if getattr(sys, "frozen", False):
    from chatxz.utils.rns_frozen import ensure_rns_interfaces
    ensure_rns_interfaces()

from chatxz.core.identity import IdentityManager
from chatxz.core.messaging import HUB_GROUP_PEER, MessagingBackend, is_hub_peer_hash
from chatxz.core.voice import VoiceRecorder, VoicePlayer
from chatxz.core.discovery import PeerDiscovery
from chatxz.core.lan_beacon import LanBeacon, BEACON_PORT
from chatxz.core.contacts import (
    contact_connect_meta,
    delete_contact as delete_saved_contact,
    migrate_contact_by_ip,
    list_contacts,
    save_contact,
)
from chatxz.core.lan_rns import (
    lan_ip_reachable,
    patch_udp_interface_unicast,
    serial_interface_online,
)
from chatxz.core.rns_interfaces import (
    INTERFACE_PRESETS,
    SERIAL_BAUD_RATES,
    SERIAL_DEFAULT_BAUD,
    ANDROID_SERIAL_PERMISSION_HINT,
    SERIAL_PERMISSION_HINT,
    serial_permission_hint_for_process,
    add_interface,
    set_primary_lan_transport,
    configured_serial_port,
    delete_interface,
    dedupe_serial_interfaces,
    ensure_runtime_serial,
    lan_discovery_configured,
    configured_serial_enabled,
    configured_tcp_lan_enabled,
    configured_udp_lan_enabled,
    ensure_runtime_tcp_lan_server,
    remove_serial_interfaces,
    prune_dead_serial_interfaces,
    list_serial_ports,
    android_standalone_needs_udp,
    standalone_needs_udp,
    normalize_interface_list,
    render_rns_config,
    serial_port_accessible,
    serial_port_status,
    serial_runtime_active,
    update_interface,
    tcp_client_target_warning,
    user_has_serial_group_access,
)
from chatxz.utils.helpers import (
    get_config_dir,
    get_data_dir,
    format_speed,
    media_type_for_filename,
    safe_basename,
    safe_path_under,
    safe_rel_path_under,
)
from chatxz.utils.debug_log import (
    debug_log_path,
    debug_log_tail,
    export_debug_logs,
    list_debug_log_files,
)
from chatxz.utils.android_notify import show_message_notification
from chatxz.utils.platform import (
    is_android,
    apply_lan_interface_preference,
    enumerate_lan_interfaces,
    get_lan_interface_preference,
    lan_connected,
    lan_ip as platform_lan_ip,
    lan_broadcast,
    physical_lan_reachable,
    desktop_lan_status,
    invalidate_desktop_interface_cache,
    parse_lan_interface_value,
    set_lan_interface_preference,
    android_storage_dirs,
    patch_embedded_signals,
    list_network_interfaces,
)
from chatxz.utils.system import get_avg_cpu_temperature, get_cpu_percent
from chatxz._version import __version__ as APP_VERSION

CONFIG_DIR = get_config_dir()
DATA_DIR = get_data_dir()
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
NETWORK_STATS_AUTO_RESET_SEC = 7 * 86400
SESSION_SYSTEM_LINK_CLOSED_TTL = 600

def build_desktop_rns_config(broadcast_ip="255.255.255.255"):
    return f"""[reticulum]
enable_transport = Yes
share_instance = No

[logging]
loglevel = 3

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

def _patch_rns_forward_ip(config_text, broadcast_ip):
    if not broadcast_ip:
        return config_text
    if "forward_ip" in config_text:
        return re.sub(r"forward_ip\s*=\s*[^\n]+", f"forward_ip = {broadcast_ip}", config_text)
    return config_text


def detect_lan_ip():
    return platform_lan_ip()

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


def _win_subprocess_flags():
    if sys.platform != "win32":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _proc_cmdline(pid):
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                [
                    "powershell.exe", "-NoProfile", "-Command",
                    f"(Get-CimInstance Win32_Process -Filter \"ProcessId={int(pid)}\").CommandLine",
                ],
                capture_output=True, text=True, timeout=5,
                creationflags=_win_subprocess_flags(),
            )
            return (result.stdout or "").strip()
        except (ValueError, subprocess.TimeoutExpired, OSError):
            return ""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def _is_chatxz_process(pid):
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
                creationflags=_win_subprocess_flags(),
            )
            line = (result.stdout or "").lower()
            return "chatxz" in line and "grok" not in line
        except (ValueError, subprocess.TimeoutExpired, OSError):
            return False
    cmd = _proc_cmdline(pid)
    return "chatxz" in cmd and "grok" not in cmd.lower()


def _port_holder_pids(port, udp=True):
    pids = []
    needle = f":{port}"
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, text=True, timeout=5,
                creationflags=_win_subprocess_flags(),
            )
            proto = "UDP" if udp else "TCP"
            for line in result.stdout.splitlines():
                upper = line.upper()
                if proto not in upper or needle not in line:
                    continue
                parts = line.split()
                if not parts:
                    continue
                try:
                    pid = int(parts[-1])
                except ValueError:
                    continue
                if pid > 0:
                    pids.append(pid)
        except (subprocess.TimeoutExpired, OSError):
            pass
        return list(dict.fromkeys(pids))
    try:
        flag = "-u" if udp else "-t"
        result = subprocess.run(
            ["ss", "-H", "-n", flag, "-lp"],
            capture_output=True, text=True, timeout=3,
        )
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


def stop_stale_chatxz_servers(exclude_pid=None):
    """Stop other chatxz server/cli processes holding RNS ports."""
    if is_android():
        return 0
    exclude_pid = exclude_pid or os.getpid()
    targets = set()
    for port in (4242, 8742):
        for pid in _port_holder_pids(port, udp=(port == 4242)):
            if pid != exclude_pid and _is_chatxz_process(pid):
                targets.add(pid)
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq chatxz.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
                creationflags=_win_subprocess_flags(),
            )
            for line in result.stdout.splitlines():
                if "chatxz.exe" not in line.lower():
                    continue
                parts = [p.strip('"') for p in line.split('","')]
                if len(parts) < 2:
                    continue
                try:
                    pid = int(parts[1])
                except ValueError:
                    continue
                if pid != exclude_pid:
                    targets.add(pid)
        except (subprocess.TimeoutExpired, OSError):
            pass
    else:
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
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    capture_output=True, timeout=5,
                    creationflags=_win_subprocess_flags(),
                )
            else:
                os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except (PermissionError, subprocess.TimeoutExpired, OSError):
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
    chatxz_holders = [p for p in holders if _is_chatxz_process(p)]

    if chatxz_holders or force:
        stop_stale_chatxz_servers()
        time.sleep(0.5)
        if not _is_port_in_use(4242):
            return True

    holders = _port_holder_pids(4242, udp=True)
    holder_txt = ", ".join(f"PID {p} ({_proc_cmdline(p)[:60]})" for p in holders) or "unknown"
    print(f"[startup] ERROR: UDP port 4242 is already in use by {holder_txt}")
    print("[startup] Another chatxz/RNS instance is probably still running.")
    if sys.platform == "win32":
        print("[startup] Close other chatxz.exe windows, or end the process in Task Manager.")
    else:
        print("[startup] Stop it with:  pkill -f chatxz.web.server")
        hint = "run.bat web --share --force" if sys.platform == "win32" else "./run.sh web --share --force"
        print(f"[startup] Or restart with:  {hint}")
    return False


def _rns_startup_failure(msg):
    """Fatal RNS startup errors must not call sys.exit from a worker thread."""
    print(f"[startup] {msg}")
    raise RuntimeError(msg)


def _pick_directory_tkinter(start):
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except tk.TclError:
            pass
        picked = filedialog.askdirectory(initialdir=start, mustexist=True, parent=root)
        root.destroy()
        return picked or None
    except Exception:
        return None


class ChatWebServer:
    def __init__(self, host="127.0.0.1", port=8742, verbose=False, debug=False, force=False, embedded=False):
        self.host = host
        self.port = port
        self.verbose = verbose
        self.debug = debug
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
        self._prune_ephemeral_history_disk()

        self.active_peer = None
        self.destination_hash = None
        self.discovery = None
        self.lan_beacon = None
        self._loop = None
        self.rns_init_error = None
        self._announce_lock = threading.Lock()
        self._last_announce_at = 0.0
        self._reverse_connect_last = {}
        self._session_resume_last = 0.0
        self._shutting_down = False
        self._failover_task = None
        self._background_tasks = []
        self._progress_last = {}
        self._progress_throttle_ms = 250
        self._ui_state = {"viewing_peer": None, "hidden": False}

    @staticmethod
    def _clean_hash(h):
        return (h or "").replace("<", "").replace(">", "").replace(":", "").strip()

    async def _run_blocking(self, fn, *args):
        if self._shutting_down:
            return None
        try:
            return await asyncio.to_thread(fn, *args)
        except asyncio.CancelledError:
            if self._shutting_down:
                return None
            raise

    async def _on_shutdown(self, app):
        self._shutting_down = True
        if self.messaging:
            self.messaging.shutdown_requested = True
            self.messaging.running = False
        for task in list(self._background_tasks):
            task.cancel()

    async def _on_cleanup(self, app):
        self._shutting_down = True
        if self.messaging:
            self.messaging.shutdown_requested = True
            self.messaging.running = False
            try:
                self.messaging._teardown_active_link()
                self.messaging.stop()
            except Exception:
                pass
        for ws in list(self.websockets):
            try:
                await ws.close()
            except Exception:
                pass
        self.websockets.clear()
        print("[shutdown] Server stopped")

    async def _wait_for_rns(self, timeout=90.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.rns_init_error:
                return False, "Network error: " + self.rns_init_error.splitlines()[-1]
            if self.messaging and self.messaging.destination:
                return True, None
            await asyncio.sleep(0.5)
        if self.embedded and not self.rns_init_error:
            return False, "Network stack still starting - wait a few seconds and try again"
        return False, "not ready"

    def _reset_connection_state(self):
        """Clear peer session on server start - UI reconnects explicitly."""
        if self.messaging and self.messaging.active_link:
            try:
                self.messaging.active_link.teardown()
            except Exception:
                pass
            self.messaging.active_link = None
        self.active_peer = None

    def _peer_dest_hash(self, any_hash):
        if any_hash in (HUB_GROUP_PEER, "__hub_group__"):
            return HUB_GROUP_PEER
        if self.messaging:
            return self.messaging.dest_hash_for(any_hash)
        return self._clean_hash(any_hash).lower()

    def _my_sender_hash(self):
        from chatxz.core.discovery import normalize_hash
        if self.messaging and self.messaging.my_dest_hash:
            return normalize_hash(self.messaging.my_dest_hash)
        if self.identity_mgr:
            connect = self.identity_mgr.get_connect_hash()
            if connect:
                return connect
        return normalize_hash(self._clean_hash(self.destination_hash or ""))

    def _is_self_hash(self, h):
        from chatxz.core.discovery import normalize_hash
        clean = normalize_hash(h)
        if not clean:
            return False
        my_connect = normalize_hash(
            (self.messaging.my_dest_hash if self.messaging else None)
            or (self.identity_mgr.get_connect_hash() if self.identity_mgr else "")
            or self._clean_hash(self.destination_hash or "")
        )
        my_ident = normalize_hash(self.identity_mgr.get_hex_hash() if self.identity_mgr else "")
        return clean in (my_connect, my_ident)

    def _peers_equivalent(self, hash_a, hash_b):
        if self.messaging:
            return self.messaging.hashes_equivalent(hash_a, hash_b)
        from chatxz.core.discovery import normalize_hash
        return normalize_hash(hash_a) == normalize_hash(hash_b)

    def _peer_alias_list(self, peer_hash):
        if self.messaging:
            return self.messaging.peer_aliases_for(peer_hash)
        clean = self._peer_dest_hash(peer_hash)
        return [clean] if clean else []

    def _session_chat_peer(self, sender_hash=None):
        viewing = self._ui_state.get("viewing_peer")
        if viewing and not is_hub_peer_hash(viewing):
            resolved = self._peer_dest_hash(viewing)
            if resolved and resolved != "unknown" and not is_hub_peer_hash(resolved):
                return resolved
        if self.messaging and self.messaging.active_peer_hash:
            if not is_hub_peer_hash(self.messaging.active_peer_hash):
                resolved = self._peer_dest_hash(self.messaging.active_peer_hash)
                if resolved and resolved != "unknown" and not is_hub_peer_hash(resolved):
                    return resolved
        if self.active_peer and not is_hub_peer_hash(self.active_peer):
            resolved = self._peer_dest_hash(self.active_peer)
            if resolved and resolved != "unknown" and not is_hub_peer_hash(resolved):
                return resolved
        if sender_hash and not is_hub_peer_hash(sender_hash):
            return self._peer_dest_hash(sender_hash)
        return ""

    def _discovery_scope_ip(self):
        settings = self.load_settings()
        if settings.get("hub_role", "off") != "off":
            return None
        if not lan_discovery_configured(settings.get("rns_interfaces")):
            return None
        pinned = (settings.get("lan_interface") or "").strip()
        if not pinned:
            return None
        name, ip = parse_lan_interface_value(pinned)
        if ip:
            return ip
        for entry in enumerate_lan_interfaces():
            if entry.get("name") == (name or pinned):
                entry_ip = entry.get("ip")
                if entry_ip and entry_ip != "disconnected":
                    return entry_ip
        return detect_lan_ip()

    def _interfaces_for_picker(self, refresh=False):
        """All local NICs/IPv4 addresses for setup/settings dropdowns (unfiltered)."""
        if refresh:
            invalidate_desktop_interface_cache()
        seen = set()
        entries = []
        for entry in enumerate_lan_interfaces():
            name = entry.get("name")
            ip = entry.get("ip") or "disconnected"
            if not name:
                continue
            key = (name, ip)
            if key in seen:
                continue
            seen.add(key)
            entries.append(entry)
        if is_android():
            ip = detect_lan_ip()
            if ip and not any(e.get("ip") == ip for e in entries):
                parts = ip.split(".")
                subnet = (
                    f"{parts[0]}.{parts[1]}.{parts[2]}.255"
                    if len(parts) == 4 else None
                )
                entries.append({
                    "name": "active",
                    "kind": "wifi",
                    "ip": ip,
                    "broadcast": subnet,
                    "subnet_broadcast": subnet,
                    "up": True,
                })
        entries.sort(key=lambda e: (e.get("name") or "", e.get("ip") or ""))
        return entries

    def _scoped_peers(self):
        if not self.discovery:
            return []
        return self.discovery.get_peers(scope_ip=self._discovery_scope_ip())

    def _resolve_incoming_peer(self, ident_hex=None, computed_dest=None, fallback=None, link=None):
        from chatxz.core.discovery import normalize_hash

        if computed_dest and not self._is_self_hash(computed_dest):
            return self._peer_dest_hash(computed_dest)

        clean_fallback = normalize_hash(fallback)
        if clean_fallback and not self._is_self_hash(clean_fallback):
            return self._peer_dest_hash(clean_fallback)

        if ident_hex and not self._is_self_hash(ident_hex) and self.discovery:
            for p in self.discovery.get_peers():
                ph = normalize_hash(p.get("hash"))
                ih = normalize_hash(p.get("identity_hash"))
                if ident_hex == ih or ident_hex == ph:
                    return ph or ident_hex

        if self.messaging and ident_hex and not self._is_self_hash(ident_hex):
            mapped = self.messaging.dest_hash_for(ident_hex)
            if mapped and len(mapped) == 32 and not self._is_self_hash(mapped):
                return mapped

        session_peer = self._session_chat_peer()
        if (
            session_peer
            and not self._is_self_hash(session_peer)
            and not is_hub_peer_hash(session_peer)
        ):
            if not ident_hex or self.messaging and self.messaging.hashes_equivalent(
                ident_hex, session_peer
            ):
                return session_peer

        if ident_hex and not self._is_self_hash(ident_hex):
            if computed_dest and not self._is_self_hash(computed_dest):
                return self._peer_dest_hash(computed_dest)
            if self.messaging:
                canon = self.messaging.canonical_connect_hash(ident_hex, link=link)
                if canon:
                    return canon
        return ""

    def _resolve_peer_hash(self, peer_hash):
        from chatxz.core.discovery import normalize_hash, message_dest_hash_for_identity
        clean = normalize_hash(peer_hash)
        if not clean:
            return clean
        if self.messaging:
            mapped = self.messaging.dest_hash_for(clean)
            if mapped and len(mapped) == 32 and not self._is_self_hash(mapped):
                return mapped
            ident = self.messaging._identity_for_hash(clean)
            if ident:
                dest = message_dest_hash_for_identity(ident)
                if dest:
                    self.messaging.register_peer_mapping(
                        dest, normalize_hash(RNS.hexrep(ident.hash))
                    )
                    return dest
        if self.discovery:
            for p in self.discovery.get_peers():
                ph = normalize_hash(p.get("hash"))
                ih = normalize_hash(p.get("identity_hash"))
                if clean == ph or clean == ih:
                    if p.get("via") == "rns" and ph:
                        return ph
                    if self.messaging:
                        ident = self.messaging._identity_for_hash(ih or ph)
                        if ident:
                            dest = message_dest_hash_for_identity(ident)
                            if dest:
                                return dest
                    return ph or clean
        return clean

    def _received_dir(self):
        settings = self.load_settings()
        return os.path.normpath(settings.get("received_dir", os.path.join(self.config_dir, "received")))

    def _sent_dir(self):
        return os.path.normpath(os.path.join(self.config_dir, "sent"))

    def _encode_file_rel(self, rel):
        return "/".join(quote(part, safe="") for part in rel.replace("\\", "/").split("/"))

    def _file_url(self, filepath):
        if not filepath:
            return ""
        full = os.path.normpath(filepath)
        if not os.path.isfile(full):
            return ""
        received = self._received_dir()
        sent = self._sent_dir()
        if full.startswith(received + os.sep) or full == received:
            rel = os.path.relpath(full, received)
            return "/api/file/received/" + self._encode_file_rel(rel)
        if full.startswith(sent + os.sep) or full == sent:
            rel = os.path.relpath(full, sent)
            return "/api/file/sent/" + self._encode_file_rel(rel)
        default_received = os.path.normpath(os.path.join(self.config_dir, "received"))
        if full.startswith(default_received + os.sep):
            rel = os.path.relpath(full, default_received)
            return "/api/file/received/" + self._encode_file_rel(rel)
        return ""

    def _enrich_message(self, entry, outgoing=None):
        enriched = dict(entry)
        if outgoing is not None:
            enriched["outgoing"] = bool(outgoing)
        elif "outgoing" not in enriched:
            sender = self._peer_dest_hash(enriched.get("sender"))
            enriched["outgoing"] = bool(sender and sender == self._my_sender_hash())
        peer = enriched.get("chat_peer") or enriched.get("peer")
        if not peer:
            if enriched.get("outgoing"):
                peer = enriched.get("peer") or self.active_peer
            else:
                peer = enriched.get("sender")
        enriched["chat_peer"] = self._peer_dest_hash(peer)
        if enriched.get("file_name") and enriched.get("type") == "file":
            inferred = media_type_for_filename(enriched["file_name"])
            if inferred != "file":
                enriched["type"] = inferred
        if enriched.get("content") and enriched.get("type") in ("image", "video", "file", "voice"):
            url = self._file_url(enriched["content"])
            if url:
                enriched["file_url"] = url
        sender = enriched.get("sender")
        if sender and sender != "system":
            sender_name = self._peer_display_name(sender)
            if sender_name:
                enriched["sender_name"] = sender_name
        return enriched

    def _is_session_system_message(self, entry):
        if isinstance(entry, str):
            content = entry
        else:
            if entry.get("type") != "system" and entry.get("sender") != "system":
                return False
            content = entry.get("content") or ""
        return (
            content.startswith("Link established with ")
            or "Link closed" in content
            or content.startswith("Connected to ")
        )

    def _prune_stale_session_system_messages(self):
        now = time.time()
        kept = []
        for m in self.message_history:
            if not self._is_session_system_message(m):
                kept.append(m)
                continue
            content = m.get("content") or ""
            if "Link closed" in content and now - m.get("timestamp", 0) < SESSION_SYSTEM_LINK_CLOSED_TTL:
                kept.append(m)
        if len(kept) != len(self.message_history):
            self.message_history = kept
            self._save_history()

    def _session_peer_at(self, timestamp):
        session_peer = None
        for m in self.message_history:
            ts = m.get("timestamp", 0)
            if ts > timestamp:
                break
            if m.get("type") != "system":
                continue
            content = m.get("content") or ""
            if content.startswith("Link established with "):
                session_peer = self._peer_dest_hash(m.get("chat_peer") or content.split("with ", 1)[-1].strip())
            elif "Link closed" in content:
                session_peer = None
        return session_peer

    def _history_for_peer(self, peer_hash, limit=500):
        peer = self._peer_dest_hash(peer_hash)
        if peer == HUB_GROUP_PEER:
            filtered = [
                self._enrich_message(m)
                for m in self.message_history
                if m.get("hub_group") or self._peer_dest_hash(m.get("chat_peer") or m.get("peer")) == HUB_GROUP_PEER
            ]
            return filtered[-limit:]
        if not peer:
            return self.message_history[-limit:]
        filtered = []
        for m in self.message_history:
            if self._is_session_system_message(m):
                continue
            cp = self._peer_dest_hash(m.get("chat_peer") or m.get("peer"))
            if cp and self._peers_equivalent(cp, peer):
                filtered.append(self._enrich_message(m))
                continue
            sender = self._peer_dest_hash(m.get("sender"))
            if sender and self._peers_equivalent(sender, peer) and m.get("sender") != "system":
                filtered.append(self._enrich_message(m))
                continue
            if not m.get("outgoing") and m.get("sender") != "system":
                if self._is_self_hash(cp) or self._is_self_hash(sender):
                    session_peer = self._session_peer_at(m.get("timestamp", 0))
                    if session_peer and self._peers_equivalent(session_peer, peer):
                        repaired = dict(m)
                        repaired["chat_peer"] = peer
                        repaired["peer"] = peer
                        if self._is_self_hash(sender):
                            repaired["sender"] = peer
                        filtered.append(self._enrich_message(repaired, outgoing=False))
        return filtered[-limit:]

    @staticmethod
    def _is_tcp_server_iface(iface):
        return (
            iface.get("type") == "TCPServerInterface"
            or iface.get("preset") in ("tcp_server", "tcp_lan")
        )

    @staticmethod
    def _is_tcp_client_iface(iface):
        return (
            iface.get("type") == "TCPClientInterface"
            or iface.get("preset") == "tcp_client"
        )

    def _apply_hub_settings(self, settings):
        hub_role = settings.get("hub_role", "off")
        hub_host = (settings.get("hub_host") or "").strip()
        hub_port = int(settings.get("hub_port") or 4242)
        interfaces = normalize_interface_list(settings.get("rns_interfaces"))
        if hub_role == "server":
            server = None
            for iface in interfaces:
                if self._is_tcp_server_iface(iface):
                    server = iface
                    break
            if not server:
                interfaces = add_interface(interfaces, "tcp_server")
                interfaces = normalize_interface_list(interfaces)
                server = next(
                    i for i in interfaces if self._is_tcp_server_iface(i)
                )
            server["enabled"] = True
            server["type"] = "TCPServerInterface"
            server["listen_ip"] = (server.get("listen_ip") or "0.0.0.0").strip() or "0.0.0.0"
            server["listen_port"] = hub_port
            for iface in interfaces:
                if self._is_tcp_client_iface(iface):
                    iface["enabled"] = False
        elif hub_role == "client":
            if not hub_host:
                settings["rns_interfaces"] = normalize_interface_list(interfaces)
                return settings
            for iface in interfaces:
                if self._is_tcp_server_iface(iface):
                    iface["enabled"] = False
            updated = False
            for iface in interfaces:
                if self._is_tcp_client_iface(iface):
                    iface["target_host"] = hub_host
                    iface["target_port"] = hub_port
                    iface["enabled"] = True
                    iface["type"] = "TCPClientInterface"
                    updated = True
                    break
            if not updated:
                interfaces = add_interface(interfaces, "tcp_client")
                interfaces = normalize_interface_list(interfaces)
                client = next(
                    i for i in interfaces if self._is_tcp_client_iface(i)
                )
                client["target_host"] = hub_host
                client["target_port"] = hub_port
                client["enabled"] = True
        settings["rns_interfaces"] = normalize_interface_list(interfaces)
        return settings

    def _schedule_android_lan_announce_retries(self):
        """Wi-Fi may come up after WebView loads — retry beacon/RNS announce."""
        if not is_android():
            return

        def attempt(label):
            if self._shutting_down or not self.messaging:
                return
            settings = self.load_settings()
            if not lan_discovery_configured(settings.get("rns_interfaces")):
                return
            if not lan_ip_reachable():
                return
            try:
                if self.discovery:
                    self.discovery.enable_discovery(clear=False)
                self.messaging._silent_announce()
                if self.lan_beacon:
                    self.lan_beacon.send(2, True)
                print(f"[network] Android LAN announce retry ({label})")
            except Exception as exc:
                print(f"[network] Android announce retry failed ({label}): {exc}")

        for delay, label in ((2.0, "2s"), (5.0, "5s"), (12.0, "12s")):
            timer = threading.Timer(delay, attempt, args=(label,))
            timer.daemon = True
            timer.start()

    def _apply_hub_runtime(self, settings=None):
        """Hot-apply hub interfaces on a running RNS instance (Android/desktop)."""
        settings = settings or self.load_settings()
        hub_role = settings.get("hub_role", "off")
        try:
            from chatxz.core.rns_interfaces import (
                ensure_runtime_tcp_client,
                ensure_runtime_tcp_hub,
                remove_tcp_client_interfaces,
                tcp_client_interface_online,
                tcp_server_interface_online,
            )
            if hub_role == "server":
                remove_tcp_client_interfaces()
                iface = ensure_runtime_tcp_hub(settings, self.config_dir)
                if iface and self.messaging:
                    self.messaging._silent_announce()
                    self.messaging._schedule_hub_queue_drain()
                online = tcp_server_interface_online(int(settings.get("hub_port") or 4242))
                if online:
                    print(f"[hub] TCP hub server listening on 0.0.0.0:{settings.get('hub_port', 4242)}")
                else:
                    print(
                        f"[hub] TCP hub server not online yet on port "
                        f"{settings.get('hub_port', 4242)} — check hub role and restart"
                    )
            elif hub_role == "client":
                host = settings.get("hub_host") or ""
                port = int(settings.get("hub_port") or 4242)
                iface = ensure_runtime_tcp_client(settings, self.config_dir)
                if iface and self.messaging:
                    self.messaging._silent_announce()
                online = tcp_client_interface_online()
                if online:
                    print(f"[hub] TCP hub client connected to {host}:{port}")
                    if self.messaging:
                        self.messaging._schedule_hub_queue_drain()
                elif host:
                    print(f"[hub] TCP hub client connecting to {host}:{port}...")
        except Exception as exc:
            print(f"[hub] Runtime hub apply failed: {exc}")

    def load_settings(self):
        defaults = {
            "name": "",
            "history_retention": "never",
            "received_dir": os.path.join(self.config_dir, "received"),
            "network_stats_auto_reset": True,
            "network_stats_reset_at": 0,
            "lan_interface": "",
            "rns_interfaces": normalize_interface_list(None),
            "hub_role": "off",
            "hub_host": "",
            "hub_port": 4242,
            "hub_server_hash": "",
            "auto_interface_enabled": True,
            "auto_announce": False,
            "setup_complete": False,
        }
        try:
            with open(SETTINGS_FILE) as f:
                s = json.load(f)
                for key, val in defaults.items():
                    s.setdefault(key, val)
                needs_udp = standalone_needs_udp(
                    s.get("rns_interfaces"), s.get("hub_role", "off")
                )
                if needs_udp:
                    s["rns_interfaces"] = normalize_interface_list(None)
                    self.save_settings(s)
                repaired = normalize_interface_list(s.get("rns_interfaces"))
                if repaired != s.get("rns_interfaces"):
                    s["rns_interfaces"] = repaired
                    self.save_settings(s)
                    self._write_rns_config(s)
                apply_lan_interface_preference(self.config_dir)
                return s
        except:
            apply_lan_interface_preference(self.config_dir)
            return dict(defaults)

    def save_settings(self, settings):
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=2)

    def _history_file(self):
        return os.path.join(self.config_dir, "history.json")

    def _history_peer(self, entry):
        if not entry:
            return ""
        return self._peer_dest_hash(entry.get("chat_peer") or entry.get("peer"))

    def _should_persist_history(self, peer_hash):
        peer = self._peer_dest_hash(peer_hash)
        if not peer or peer == "unknown":
            return False
        return True

    def _persisted_history_entries(self):
        return [
            m for m in self.message_history
            if self._should_persist_history(self._history_peer(m))
        ]

    def _load_history(self):
        try:
            with open(self._history_file()) as f:
                loaded = json.load(f)
            return [
                m for m in loaded
                if self._should_persist_history(self._history_peer(m))
            ]
        except:
            return []

    def _save_history(self):
        try:
            with open(self._history_file(), "w") as f:
                json.dump(self._persisted_history_entries()[-1000:], f)
        except:
            pass

    def _prune_ephemeral_history_disk(self):
        """Drop non-contact chat history from disk (e.g. after app restart on Android)."""
        self._save_history()

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

    def _interfaces_for_api(self, interfaces):
        rows = []
        for iface in normalize_interface_list(interfaces):
            row = dict(iface)
            if iface.get("preset") == "serial" or iface.get("type") == "SerialInterface":
                row["port_status"] = serial_port_status(iface.get("port"))
                row["port_accessible"] = serial_port_accessible(iface.get("port"))
                row["serial_active"] = serial_runtime_active(iface)
            rows.append(row)
        return rows

    def _write_rns_config(self, settings=None):
        settings = settings or self.load_settings()
        rns_config_path = os.path.join(self.config_dir, "config")
        os.makedirs(self.config_dir, exist_ok=True)
        bcast = lan_broadcast()
        interfaces = normalize_interface_list(settings.get("rns_interfaces"))
        settings["rns_interfaces"] = interfaces
        self.save_settings(settings)
        for iface in interfaces:
            if iface.get("type") == "UDPInterface" and bcast:
                iface["forward_ip"] = bcast
        config_text = render_rns_config(
            interfaces,
            broadcast_ip=bcast,
            android=is_android(),
            auto_interface_enabled=settings.get("auto_interface_enabled", True),
        )
        with open(rns_config_path, "w") as f:
            f.write(config_text)
        print(f"[config] Wrote RNS config at {rns_config_path} (broadcast={bcast})")
        return rns_config_path

    def _log_serial_diagnostics(self):
        try:
            import grp
            names = sorted(grp.getgrgid(g).gr_name for g in os.getgroups())
        except Exception:
            names = []
        print(f"[serial] process groups: {', '.join(names) or '(none)'}")
        print(f"[serial] dialout/uucp access: {user_has_serial_group_access()}")
        for p in list_serial_ports():
            print(f"[serial] {p.get('device')}: {p.get('status')}")
        for iface in normalize_interface_list(self.load_settings().get("rns_interfaces")):
            if iface.get("preset") == "serial" or iface.get("type") == "SerialInterface":
                port = iface.get("port") or "(none)"
                active = serial_runtime_active(iface)
                print(
                    f"[serial] configured port={port} enabled={iface.get('enabled')} "
                    f"active={active}"
                )

    def start_rns(self):
        try:
            if RNS.Reticulum.get_instance() is not None and self.messaging and self.messaging.destination:
                return RNS.hexrep(self.messaging.destination.hash)
        except Exception:
            pass
        if is_android():
            try:
                from chatxz.android_usb.bootstrap import bootstrap as bootstrap_android_usb
                bootstrap_android_usb()
            except Exception as e:
                print(f"[serial] Android USB bootstrap failed: {e}")
        # v0.3.90+ starts RNS on a worker thread on desktop; RNS registers SIGINT handlers.
        patch_embedded_signals()
        settings = self._apply_hub_settings(self.load_settings())
        self._write_rns_config(settings)
        self._log_serial_diagnostics()

        if not ensure_rns_ports_free(force=self.force):
            msg = "UDP port 4242 is already in use"
            if self.embedded:
                raise RuntimeError(msg)
            _rns_startup_failure(msg)

        if self.debug and not (self.embedded or is_android()):
            loglevel = getattr(RNS, "LOG_EXTREME", RNS.LOG_DEBUG)
            print("[startup] Debug logging enabled (RNS extreme + chatxz trace)")
        elif self.debug or self.verbose:
            loglevel = RNS.LOG_DEBUG
            print("[startup] Verbose logging enabled (RNS debug)")
        else:
            loglevel = RNS.LOG_NOTICE
        if getattr(sys, "frozen", False):
            from chatxz.utils.rns_frozen import ensure_rns_interfaces
            ensure_rns_interfaces()
        def _start_reticulum():
            return RNS.Reticulum(self.config_dir, loglevel=loglevel)

        try:
            _start_reticulum()
        except (OSError, Exception) as e:
            err = str(e)
            if "reinitialise" in err and self.messaging and self.messaging.destination:
                print("[RNS] Already running - reusing existing instance")
                return RNS.hexrep(self.messaging.destination.hash)
            print(f"[RNS] Startup error: {e}")
            if is_android():
                raise RuntimeError(f"RNS failed to start: {e}") from e
            if any(
                token in err.lower()
                for token in ("address already in use", "errno 48", "errno 10048", "eaddrinuse")
            ):
                print("[RNS] Duplicate interface or port conflict — repairing config...")
                settings = self.load_settings()
                settings["rns_interfaces"] = normalize_interface_list(
                    settings.get("rns_interfaces")
                )
                self._write_rns_config(settings)
            print("[RNS] Retrying after stopping stale instances...")
            stop_stale_chatxz_servers(exclude_pid=os.getpid())
            time.sleep(1)
            if not ensure_rns_ports_free(force=True):
                msg = "UDP port 4242 is already in use — close other chatxz windows"
                if self.embedded:
                    raise RuntimeError(msg)
                _rns_startup_failure(msg)
            try:
                _start_reticulum()
            except Exception as retry_exc:
                if self.embedded:
                    raise RuntimeError(f"RNS init failed: {retry_exc}") from retry_exc
                _rns_startup_failure(f"RNS init failed: {retry_exc}")
        settings = self.load_settings()
        apply_lan_interface_preference(self.config_dir)
        interfaces = settings.get("rns_interfaces")
        if configured_udp_lan_enabled(interfaces):
            patch_udp_interface_unicast()
        elif configured_tcp_lan_enabled(interfaces):
            print("[network] TCP LAN mode — beacon discovery active, direct TCP dial on connect")
        else:
            print("[network] LAN transport not configured — skipping beacon/unicast helpers")
        self.identity = self.identity_mgr.load_or_create()
        my_ip = detect_lan_ip()
        if my_ip and lan_discovery_configured(interfaces):
            print(f"[network] Detected LAN IP: {my_ip}")
        elif configured_serial_enabled(interfaces) and not lan_discovery_configured(interfaces):
            print("[network] Serial-only transport — LAN IP detection skipped")
        received_dir = settings.get("received_dir", os.path.join(self.config_dir, "received"))
        auto_announce = bool(settings.get("auto_announce", False)) or (
            configured_serial_enabled(interfaces)
            and not lan_discovery_configured(interfaces)
        )
        self.messaging = MessagingBackend(
            self.identity, self.config_dir,
            on_message=self._on_message,
            on_progress=self._on_transfer_progress,
            on_link_established=self._on_link_established,
            on_link_closed=self._on_link_closed,
            on_queue_sent=self._on_queue_sent,
            display_name=settings.get("name", ""),
            auto_announce=auto_announce,
            receive_dir=received_dir,
            peer_resolver=self._resolve_incoming_peer,
        )
        self.voice_recorder = VoiceRecorder(self.config_dir)
        dest = self.messaging.start()
        sent_ids = [
            m.get("msg_id") for m in self.message_history
            if m.get("msg_id") and m.get("status") == "sent"
        ]
        pruned = self.messaging.prune_stale_queue(sent_ids)
        if pruned:
            print(f"[queue] Pruned {pruned} stale item(s) already marked sent")

        my_hash = RNS.hexrep(dest.hash)
        my_dest_clean = my_hash.replace(":", "")
        self.messaging.my_dest_hash = my_dest_clean
        self.destination_hash = my_hash
        self.discovery = PeerDiscovery(
            on_peer_seen=self._on_peer_discovered,
            on_peer_evicted=self._on_peer_evicted,
        )
        self.discovery.start()
        if lan_discovery_configured(interfaces):
            self.discovery.enable_discovery(clear=False)
        identity_pubkey = None
        if self.identity:
            try:
                identity_pubkey = self.identity.get_public_key()
            except Exception:
                identity_pubkey = None
        if lan_discovery_configured(interfaces):
            self.lan_beacon = LanBeacon(
                self.discovery,
                my_dest_clean,
                display_name=settings.get("name", ""),
                ip=my_ip,
                port=self.port,
                periodic=auto_announce,
                identity_hash=self.identity_mgr.get_hex_hash(),
                identity_pubkey=identity_pubkey,
                on_periodic=self._on_beacon_periodic if auto_announce else None,
            )
            self.lan_beacon.start()
        else:
            self.lan_beacon = None
            print("[network] Serial/other-only mode — LAN beacon disabled")
        if auto_announce:
            print("[network] Auto-announce on — periodic LAN discovery active")
        else:
            print("[network] Auto-announce off — tap Announce to discover peers")

        serial_hot = None
        for attempt in range(3):
            serial_hot = ensure_runtime_serial(settings.get("rns_interfaces"))
            if serial_hot:
                break
            if attempt < 2:
                time.sleep(0.5)
        dedupe_serial_interfaces()
        if serial_hot:
            print(f"[serial] Runtime serial interface active on {getattr(serial_hot, 'port', '?')}")
        elif configured_serial_port(settings.get("rns_interfaces"))[0]:
            print("[serial] Warning: serial port configured but RNS SerialInterface is not active")

        try:
            from chatxz.core.lan_rns import prune_stale_lan_paths
            prune_stale_lan_paths()
            if configured_serial_enabled(interfaces) and not lan_discovery_configured(interfaces):
                self.messaging._burst_serial_announce()
                print("[network] Startup RNS announce burst on serial")
            else:
                self.messaging._silent_announce()
            if not auto_announce:
                print("[network] Startup announce queued (tap Announce for more)")

            def _deferred_startup_announce():
                try:
                    if (
                        lan_discovery_configured(interfaces)
                        and lan_ip_reachable()
                        and self.lan_beacon
                    ):
                        self.lan_beacon.send(1, subnet_probe=False)
                        if not auto_announce:
                            print("[network] Startup announce sent once (tap Announce for more)")
                except Exception as exc:
                    print(f"[network] Startup announce failed: {exc}")

            threading.Thread(
                target=_deferred_startup_announce,
                name="chatxz-startup-announce",
                daemon=True,
            ).start()
        except Exception as exc:
            print(f"[network] Startup announce failed: {exc}")

        if configured_tcp_lan_enabled(interfaces) and settings.get("hub_role", "off") == "off":
            tcp_srv = ensure_runtime_tcp_lan_server(settings, self.config_dir)
            if tcp_srv:
                print(f"[tcp-lan] TCP LAN server listening on 0.0.0.0:{getattr(tcp_srv, 'listen_port', 4242)}")
        self._apply_hub_runtime(settings)
        if is_android() and lan_discovery_configured(interfaces):
            self._schedule_android_lan_announce_retries()
        if settings.get("hub_role") == "server":
            hub_hash = my_dest_clean
            if settings.get("hub_server_hash") != hub_hash:
                settings["hub_server_hash"] = hub_hash
                self.save_settings(settings)

        return my_hash

    def _on_message(self, chat_msg, sender_hash):
        hub_group = bool(getattr(chat_msg, "hub_group", False))
        if hub_group:
            chat_peer = HUB_GROUP_PEER
            if sender_hash and sender_hash != "system":
                sender = self._peer_dest_hash(sender_hash)
                if self.messaging:
                    sender = (
                        self.messaging.canonical_connect_hash(sender_hash)
                        or sender
                    )
            else:
                sender = "system"
        elif sender_hash and sender_hash != "system":
            if self.messaging:
                chat_peer = (
                    self.messaging.canonical_connect_hash(sender_hash)
                    or self._peer_dest_hash(sender_hash)
                )
            else:
                chat_peer = self._peer_dest_hash(sender_hash)
            sender = chat_peer
        else:
            chat_peer = self._peer_dest_hash(self.active_peer)
            sender = "system"
        entry = self._enrich_message({
            "type": chat_msg.msg_type,
            "content": chat_msg.content,
            "sender": sender,
            "peer": chat_peer,
            "chat_peer": chat_peer,
            "timestamp": chat_msg.timestamp,
            "file_name": chat_msg.file_name,
            "file_size": chat_msg.file_size,
            "msg_id": chat_msg.msg_id,
            "hub_group": hub_group,
            "status": "received" if sender_hash and sender_hash != "system" else "",
        }, outgoing=False)
        if self._is_session_system_message(chat_msg.content or ""):
            return
        self.message_history.append(entry)
        self._save_history()
        if self.debug:
            print(f"[chat] recv type={entry['type']} peer={entry.get('chat_peer', '')[:16]} msg_id={entry.get('msg_id', '')[:8]}")
        if self.websockets and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._broadcast({"type": "message", "data": entry}),
                self._loop
            )
        settings = self.load_settings()
        if hub_group and settings.get("hub_role") == "server" and self.messaging and sender_hash:
            self.messaging.relay_hub_message(chat_msg, sender_hash)
        notify_peer = HUB_GROUP_PEER if hub_group else chat_peer
        if sender_hash and sender_hash != "system" and self._should_android_notify(notify_peer, entry):
            preview = self._notification_preview(entry)
            if hub_group:
                name = "Group chat"
            else:
                name = self._contact_name_for(chat_peer) or chat_peer[:8]
            show_message_notification(name, preview, notify_peer)

    def _queue_target_hash(self):
        viewing = self._ui_state.get("viewing_peer")
        if viewing:
            return self._peer_dest_hash(viewing)
        return (
            self._session_chat_peer()
            or self._peer_dest_hash(self.active_peer)
            or getattr(self.messaging, "_session_peer_hash", None)
        )

    def _is_saved_contact(self, peer_hash):
        for contact in list_contacts(self.config_dir):
            if self._peers_equivalent(contact.get("hash"), peer_hash):
                return True
        return False

    def _clear_queue_for_peer(self, peer_hash):
        if not self.messaging:
            return 0
        before = self.messaging.queue_size()
        self.messaging.clear_queue(self._peer_dest_hash(peer_hash))
        return before - self.messaging.queue_size()

    def _purge_ephemeral_peer(self, peer_hash):
        peer = self._peer_dest_hash(peer_hash)
        if not peer or self._is_saved_contact(peer):
            return 0
        removed = self._clear_history_for_peer(peer)
        self._clear_queue_for_peer(peer)
        return removed

    def _enable_discovery(self, clear=False):
        if self.discovery:
            self.discovery.enable_discovery(clear=clear)

    def _on_beacon_periodic(self):
        if self.messaging and not self.messaging.shutdown_requested:
            try:
                self.messaging._silent_announce()
            except Exception:
                pass

    def _apply_auto_announce_settings(self, settings):
        enabled = bool(settings.get("auto_announce", False))
        if self.messaging:
            self.messaging.auto_announce = enabled
        if enabled and self.discovery:
            self.discovery.enable_discovery(clear=False)
        if self.lan_beacon:
            self.lan_beacon.set_periodic(
                enabled,
                on_periodic=self._on_beacon_periodic if enabled else None,
            )

    def _contact_name_for(self, peer_hash):
        for contact in list_contacts(self.config_dir):
            if self._peers_equivalent(contact.get("hash"), peer_hash):
                return contact.get("name") or ""
        if self.discovery:
            for peer in self.discovery.get_peers():
                if self._peers_equivalent(peer.get("hash"), peer_hash):
                    name = (peer.get("name") or "").strip()
                    if name and name != peer_hash[:8]:
                        return name
        settings = self.load_settings()
        my_hash = self._my_sender_hash()
        if settings.get("name") and self._peers_equivalent(peer_hash, my_hash):
            return settings.get("name")
        return ""

    def _peer_display_name(self, peer_hash):
        if not peer_hash or peer_hash == "system":
            return ""
        name = self._contact_name_for(peer_hash)
        if name:
            return name
        clean = self._peer_dest_hash(peer_hash)
        return clean[:8] if clean else ""

    def _notification_preview(self, entry):
        msg_type = entry.get("type", "text")
        if msg_type in ("text", "emoji"):
            return (entry.get("content") or "New message")[:120]
        return entry.get("file_name") or msg_type or "New message"

    def _should_android_notify(self, peer_hash, entry):
        if not is_android() or entry.get("type") == "system":
            return False
        vp = self._ui_state.get("viewing_peer")
        hidden = self._ui_state.get("hidden", True)
        if vp and self._peers_equivalent(vp, peer_hash) and not hidden:
            return False
        return True

    def _on_queue_sent(self, chat_msg, target_hash, queue_entry):
        my_hash = self._my_sender_hash()
        chat_peer = self._peer_dest_hash(target_hash) if target_hash else (
            self._session_chat_peer() or self._peer_dest_hash(self.active_peer)
        )
        msg_id = chat_msg.msg_id or queue_entry.get("msg_id")
        file_name = chat_msg.file_name or queue_entry.get("file_name")
        file_size = chat_msg.file_size or queue_entry.get("file_size")
        updated = False
        for item in self.message_history:
            if item.get("msg_id") == msg_id:
                item["status"] = "sent"
                item["timestamp"] = chat_msg.timestamp
                if file_name:
                    item["file_name"] = file_name
                if file_size:
                    item["file_size"] = file_size
                updated = True
                break
        if not updated:
            entry = self._enrich_message({
                "type": chat_msg.msg_type,
                "content": chat_msg.content,
                "sender": my_hash,
                "peer": chat_peer,
                "chat_peer": chat_peer,
                "timestamp": chat_msg.timestamp,
                "msg_id": msg_id,
                "file_name": file_name,
                "file_size": file_size,
                "status": "sent",
            }, outgoing=True)
            self.message_history.append(entry)
        else:
            entry = next(i for i in self.message_history if i.get("msg_id") == msg_id)
        self._save_history()
        if self.websockets and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._broadcast({"type": "message", "data": entry}),
                self._loop
            )
            asyncio.run_coroutine_threadsafe(
                self._broadcast({
                    "type": "queue_cleared",
                    "data": {"count": self.messaging.queue_size() if self.messaging else 0},
                }),
                self._loop,
            )

    def _prune_websockets(self):
        """Drop closed sockets (Android WebView reloads leave zombie connections)."""
        dead = [ws for ws in list(self.websockets) if ws.closed]
        for ws in dead:
            self.websockets.discard(ws)
        return len(self.websockets)

    def _ws_client_count(self):
        return self._prune_websockets()

    async def _broadcast(self, data):
        msg = json.dumps(data)
        for ws in self.websockets.copy():
            if ws.closed:
                self.websockets.discard(ws)
                continue
            try:
                await ws.send_str(msg)
            except Exception:
                self.websockets.discard(ws)

    def _current_peer_for_ip(self, ip):
        if not ip or not self.discovery:
            return None
        best = None
        for peer in self.discovery.get_peers():
            if peer.get("ip") != ip:
                continue
            if not best or peer.get("last_seen", 0) >= best.get("last_seen", 0):
                best = peer
        return best

    def _peer_is_current(self, peer_hash):
        clean = self._peer_dest_hash(peer_hash)
        if not clean:
            return False
        if self.messaging:
            if self.messaging.active_peer_hash and self._peers_equivalent(
                clean, self.messaging.active_peer_hash
            ):
                return True
            for linked in self.messaging.linked_peers():
                if self._peers_equivalent(clean, linked):
                    return True
        if self.discovery:
            return self.discovery.peer_is_current(clean)
        return False

    def _resolve_current_peer_hash(self, peer_hash, peer_ip=None):
        clean = self._peer_dest_hash(peer_hash)
        if self._peer_is_current(clean):
            return clean
        if peer_ip:
            current = self._current_peer_for_ip(peer_ip)
            if current:
                return self._peer_dest_hash(current.get("hash"))
        if self.discovery:
            for peer in self.discovery.get_peers():
                if self._peers_equivalent(peer.get("hash"), clean):
                    return self._peer_dest_hash(peer.get("hash"))
                if peer.get("identity_hash") and self._peers_equivalent(
                    peer.get("identity_hash"), clean
                ):
                    return self._peer_dest_hash(peer.get("hash"))
        return clean

    def _on_peer_evicted(self, removed_hashes, new_peer=None):
        if not removed_hashes:
            return
        self._supersede_peer_hashes(removed_hashes, new_peer)

    def _supersede_peer_hashes(self, removed_hashes, new_peer=None):
        from chatxz.core.discovery import normalize_hash

        removed_clean = []
        for raw in removed_hashes:
            clean = self._peer_dest_hash(raw)
            if clean:
                removed_clean.append(clean)
        if not removed_clean:
            return

        replacement = None
        if new_peer:
            replacement = self._peer_dest_hash(new_peer.get("hash"))
            ip = new_peer.get("ip")
            if ip:
                migrate_contact_by_ip(
                    self.config_dir,
                    ip,
                    replacement,
                    name=new_peer.get("name"),
                    port=new_peer.get("port"),
                    identity_hash=new_peer.get("identity_hash"),
                )

        try:
            from chatxz.core.peer_identity import purge_rns_paths_for_hashes
            purge_rns_paths_for_hashes(removed_clean)
        except Exception:
            pass

        for old in removed_clean:
            if self.messaging:
                self.messaging.disconnect_peer(old)
                self.messaging.clear_queue(old)
            delete_saved_contact(self.config_dir, old)
            self._clear_history_for_peer(old)
            self._clear_queue_for_peer(old)
            if self.active_peer and self._peers_equivalent(self.active_peer, old):
                self.active_peer = replacement
            if self._ui_state.get("viewing_peer") and self._peers_equivalent(
                self._ui_state.get("viewing_peer"), old
            ):
                self._ui_state["viewing_peer"] = replacement

        if self.websockets and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._broadcast({
                    "type": "peer_superseded",
                    "data": {
                        "removed": removed_clean,
                        "replacement": replacement,
                        "replacement_peer": new_peer,
                    },
                }),
                self._loop,
            )
            peers = self._scoped_peers()
            asyncio.run_coroutine_threadsafe(
                self._broadcast({"type": "peers", "data": peers}),
                self._loop,
            )
        print(
            f"[discovery] Superseded {len(removed_clean)} stale peer hash(es)"
            + (f" -> {replacement[:16]}..." if replacement else "")
        )

    def _on_peer_discovered(self, peer):
        if not self.messaging:
            return
        from chatxz.core.discovery import register_identity_from_peer
        register_identity_from_peer(peer)
        dest = self.messaging._hash_from_peer_info(peer) or self._peer_dest_hash(peer.get("hash"))
        if dest and dest != peer.get("hash"):
            peer = dict(peer)
            peer["hash"] = dest
        if peer.get("identity_hash"):
            self.messaging.register_peer_mapping(dest, peer.get("identity_hash"))
        if peer.get("ip") and any(
            (c.get("ip") or "").strip() == peer.get("ip")
            for c in list_contacts(self.config_dir)
        ):
            migrate_contact_by_ip(
                self.config_dir,
                peer.get("ip"),
                dest,
                name=peer.get("name"),
                port=peer.get("port"),
                identity_hash=peer.get("identity_hash"),
            )
        if self.discovery and self.websockets and self._loop:
            peers = self._scoped_peers()
            asyncio.run_coroutine_threadsafe(
                self._broadcast({"type": "peers", "data": peers}),
                self._loop
            )

    def _on_link_closed(self, peer_hash, handoff=False):
        if handoff or getattr(self.messaging, "_failover_in_progress", False):
            return
        peer = self._peer_dest_hash(peer_hash)
        still_linked = bool(self.messaging and peer and self.messaging._peer_link_active(peer))
        removed = 0
        if (
            peer
            and self.active_peer
            and self._peers_equivalent(peer, self.active_peer)
            and not still_linked
        ):
            self.active_peer = (
                self.messaging.active_peer_hash if self.messaging else None
            )
        if self.websockets and self._loop:
            if removed:
                asyncio.run_coroutine_threadsafe(
                    self._broadcast({
                        "type": "peer_history_cleared",
                        "data": {"peer": peer, "removed": removed},
                    }),
                    self._loop,
                )
            asyncio.run_coroutine_threadsafe(
                self._broadcast({
                    "type": "link_closed",
                    "data": {
                        "peer": peer,
                        "linked_peers": (
                            self.messaging.linked_peers() if self.messaging else []
                        ),
                    },
                }),
                self._loop
            )

    def _register_link_peer_in_discovery(self, peer_hash):
        if not self.discovery or not peer_hash:
            return
        name = self._peer_display_name(peer_hash) or peer_hash[:8]
        settings = self.load_settings()
        via = "tcp_hub" if settings.get("hub_role", "off") != "off" else "link"
        self.discovery.register_link_peer(peer_hash, name=name, via=via)

    def _maybe_update_hub_server_hash(self, peer_hash):
        settings = self.load_settings()
        if settings.get("hub_role") != "client":
            return
        clean = self._peer_dest_hash(peer_hash)
        if not clean or self._is_self_hash(clean):
            return
        if settings.get("hub_server_hash") != clean:
            settings["hub_server_hash"] = clean
            self.save_settings(settings)
            print(f"[hub] Recorded hub server hash {clean[:16]}...")

    def _on_link_established(self, peer_hash, link, background=False, promote_active=True,
                             passive=False):
        if self.messaging and link:
            resolved = self.messaging.canonical_connect_hash(peer_hash, link=link)
        else:
            resolved = self._peer_dest_hash(peer_hash)
        if (not resolved or self._is_self_hash(resolved)) and self.discovery:
            fixed = self._resolve_incoming_peer(link=link)
            if fixed and not self._is_self_hash(fixed):
                resolved = fixed
        elif not resolved:
            resolved = self._peer_dest_hash(peer_hash)
        self._register_link_peer_in_discovery(resolved)
        self._maybe_update_hub_server_hash(resolved)
        user_disconnected = bool(
            self.messaging and self.messaging.is_user_disconnected(resolved)
        )
        if passive or user_disconnected:
            promote_active = False
            background = True
        if promote_active and not passive:
            self.active_peer = resolved
        self._prune_stale_session_system_messages()
        path_switch = bool(getattr(self.messaging, "_last_handoff", False))
        label = "passive" if passive else ("background" if background else "active")
        print(f"[connect] Link with {resolved[:16]}... ({label})")
        if self.websockets and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._broadcast({
                    "type": "link_established",
                    "data": {
                        "hash": resolved,
                        "aliases": self._peer_alias_list(resolved),
                        "path_switch": path_switch,
                        "background": background,
                        "promote_active": promote_active,
                        "passive": passive,
                        "user_disconnected": user_disconnected,
                        "linked_peers": (
                            self.messaging.linked_peers() if self.messaging else []
                        ),
                    },
                }),
                self._loop
            )

    def _on_transfer_progress(self, data):
        status = data.get("status", "active")
        transfer_id = data.get("transfer_id")
        if (
            status == "active"
            and transfer_id
            and self.messaging
            and transfer_id in getattr(self.messaging, "_cancelled_transfers", set())
        ):
            return
        if status in ("complete", "cancelled", "failed"):
            self._progress_last.pop(data.get("transfer_id") or data.get("file_name"), None)
        else:
            key = data.get("transfer_id") or data.get("file_name") or "default"
            now = time.time()
            last = self._progress_last.get(key, {})
            pct = data.get("progress", 0)
            if last and (now - last.get("ts", 0)) < (self._progress_throttle_ms / 1000.0):
                if abs(pct - last.get("pct", -1)) < 1:
                    return
            self._progress_last[key] = {"ts": now, "pct": pct}
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
        candidates = []
        if getattr(sys, "frozen", False):
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                candidates.append(Path(meipass) / "chatxz" / "web" / "static")
        candidates.extend([
            Path(__file__).parent / "static",
            Path.cwd() / "chatxz" / "web" / "static",
            Path.cwd() / "static",
        ])
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
        resp = web.FileResponse(index_path)
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        return resp

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
        from chatxz.core.discovery import normalize_hash
        from chatxz.core.peer_identity import connect_hash_for_manager

        connect = ""
        if self.messaging and self.messaging.my_dest_hash:
            connect = normalize_hash(self.messaging.my_dest_hash)
        elif self.messaging and self.messaging.destination:
            connect = normalize_hash(RNS.hexrep(self.messaging.destination.hash))
        else:
            connect = connect_hash_for_manager(
                self.identity_mgr,
                getattr(self.messaging, "destination", None) if self.messaging else None,
            )
        if not connect:
            connect = normalize_hash(self.destination_hash or "")
        if not connect:
            connect = self.identity_mgr.get_connect_hash()
        identity_raw = normalize_hash(self.identity_mgr.get_hex_hash() if self.identity_mgr else "")
        contacts = list_contacts(self.config_dir)
        discovered = self._scoped_peers()
        link_active = bool(self.messaging and self.messaging.active_link)
        connected = self.active_peer if link_active and self.active_peer else None
        linked_peers = self.messaging.linked_peers() if self.messaging else []
        return web.json_response({
            "hash": connect,
            "connect_hash": connect,
            "identity_hash": identity_raw,
            "connected": connected,
            "linked_peers": linked_peers,
            "contacts": contacts,
            "discovered": discovered,
            "platform": self._platform_name(),
            "app_version": APP_VERSION,
            "rns_ready": bool(self.messaging and self.messaging.destination),
            "rns_error": self.rns_init_error,
            "debug_log_path": debug_log_path() if is_android() else None,
        })

    async def handle_add_contact(self, request):
        try:
            data = await request.json()
            peer_hash = data.get("hash", "").strip().replace(":", "")
            name = data.get("name", peer_hash).strip()
            if not peer_hash:
                return web.json_response({"error": "hash required"}, status=400)
            entry = save_contact(
                self.config_dir,
                peer_hash,
                name=name or peer_hash,
                ip=data.get("ip"),
                port=data.get("port"),
                identity_hash=data.get("identity_hash"),
            )
            return web.json_response({"status": "ok", "contact": entry})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_delete_contact(self, request):
        try:
            peer_hash = request.match_info["hash"].replace(":", "")
            if delete_saved_contact(self.config_dir, peer_hash):
                return web.json_response({"status": "ok"})
            return web.json_response({"error": "not found"}, status=404)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    def _discovery_peer_for_connect(self, peer_ip, hash_hex):
        from chatxz.core.discovery import normalize_hash
        if not self.discovery:
            return None
        clean = normalize_hash(hash_hex)
        by_hash = None
        by_rns = None
        by_ip = None
        for p in self.discovery.get_peers():
            ph = normalize_hash(p.get("hash"))
            ih = normalize_hash(p.get("identity_hash"))
            if peer_ip and p.get("ip") == peer_ip:
                by_ip = p
            if clean and (ph == clean or ih == clean):
                by_hash = p
                if p.get("via") == "rns":
                    by_rns = p
        if by_rns:
            return by_rns
        if by_hash:
            return by_hash
        if clean:
            return None
        return by_ip

    def _resolve_connect_target(self, peer_hash, peer_ip=None):
        resolved = self._resolve_peer_hash(peer_hash)
        if not self.discovery:
            return resolved
        from chatxz.core.discovery import normalize_hash
        clean = normalize_hash(resolved)
        for p in self.discovery.get_peers():
            ph = normalize_hash(p.get("hash"))
            ih = normalize_hash(p.get("identity_hash"))
            if clean and (ph == clean or ih == clean):
                return self._resolve_peer_hash(p.get("hash"))
        if peer_ip and not clean:
            for p in self.discovery.get_peers():
                if p.get("ip") == peer_ip:
                    return self._resolve_peer_hash(p.get("hash"))
        return resolved

    async def handle_connect(self, request):
        if self._shutting_down:
            return web.json_response({"error": "server shutting down"}, status=503)
        try:
            data = await request.json()
            peer_hash = data.get("hash", "").strip()
            if not peer_hash:
                return web.json_response({"error": "hash required"}, status=400)
            peer_ip = (data.get("ip") or "").strip() or None
            peer_port = data.get("port") or 8742
            self._enable_discovery(clear=False)
            settings = self.load_settings()
            configured = settings.get("rns_interfaces")
            if (
                self.messaging
                and configured_serial_enabled(configured)
                and not lan_discovery_configured(configured)
            ):
                await self._run_blocking(self.messaging._burst_serial_announce, 4, 0.3)
            resolved_hash = self._resolve_connect_target(peer_hash, peer_ip)
            resolved_hash = self._resolve_current_peer_hash(resolved_hash, peer_ip)
            hub_role = settings.get("hub_role", "off")
            if (
                self.discovery
                and hub_role == "off"
                and not self._peer_is_current(resolved_hash)
            ):
                return web.json_response({
                    "error": "Stale peer hash — use the peer in Discovered or wait for Announce",
                }, status=400)
            peer_info = self._discovery_peer_for_connect(peer_ip, resolved_hash)
            if not peer_info:
                peer_info = self._peer_in_discovery(resolved_hash, peer_ip)
            if peer_info:
                from chatxz.core.discovery import register_identity_from_peer
                if register_identity_from_peer(peer_info):
                    print(
                        f"[connect] Pre-registered identity from discovery "
                        f"({peer_info.get('ip', '?')})"
                    )
                if not peer_ip and peer_info.get("ip"):
                    peer_ip = peer_info.get("ip")
                    peer_port = peer_info.get("port") or peer_port
            peer_ip, peer_port = self._resolve_peer_connect_ip(resolved_hash, peer_ip, peer_port)
            caller_ip = detect_lan_ip() or (self.host if self.host not in ("127.0.0.1", "0.0.0.0") else "")
            if is_android() and not caller_ip:
                print("[connect] Warning: could not detect Android LAN IP - reverse connect may fail")
            ok = await self._run_blocking(
                self.messaging.connect_to,
                resolved_hash,
                peer_ip,
                peer_port,
                self._discovery_peer_for_connect,
                caller_ip,
                self.port,
                False,
                False,
                False,
                True,
            )
            if self._shutting_down or ok is None:
                return web.json_response({"error": "server shutting down"}, status=503)
            if ok:
                clean = self._peer_dest_hash(
                    self.messaging.active_peer_hash or resolved_hash
                )
                self.active_peer = clean
                return web.json_response({
                    "status": "ok",
                    "hash": clean,
                    "linked_peers": self.messaging.linked_peers(),
                })
            return web.json_response({"error": "connection failed"}, status=400)
        except asyncio.CancelledError:
            return web.json_response({"error": "server shutting down"}, status=503)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _reverse_connect_task(self, peer_hash, peer_ip, peer_port, caller_ip, caller_port):
        """Background outbound link for /api/request_connect (must return HTTP quickly)."""
        try:
            result = await self._run_blocking(
                self.messaging.connect_to,
                peer_hash,
                peer_ip,
                peer_port,
                self._discovery_peer_for_connect,
                caller_ip,
                caller_port,
                False,
                False,
                True,
            )
            if self._shutting_down or result is None:
                return
            if result:
                clean = self._peer_dest_hash(peer_hash)
                if getattr(self.messaging, "_connect_user_initiated", False):
                    self.active_peer = clean
                print(f"[connect] Outbound-connect established with {clean[:16]}...")
            else:
                await self._broadcast({"type": "connect_fail", "error": "reverse connect failed"})
        except Exception as e:
            print(f"[connect] Reverse-connect task error: {e}")
            try:
                await self._broadcast({"type": "connect_fail", "error": str(e)})
            except Exception:
                pass

    async def handle_request_connect(self, request):
        """Peer asks us to open an outbound RNS link (reverse connect for Android)."""
        ok, err = await self._wait_for_rns()
        if not ok:
            return web.json_response({"error": err or "not ready"}, status=400)
        try:
            data = await request.json()
            peer_hash = (data.get("hash") or "").strip()
            if not peer_hash:
                return web.json_response({"error": "hash required"}, status=400)
            peer_ip = (data.get("ip") or "").strip() or None
            peer_port = data.get("port") or 8742
            caller_ip = detect_lan_ip() or (self.host if self.host not in ("127.0.0.1", "0.0.0.0") else "")
            resolved = self._resolve_connect_target(peer_hash, peer_ip)
            if self.messaging and self.messaging.is_user_disconnected(resolved):
                return web.json_response({
                    "status": "ok",
                    "passive": True,
                    "connected": False,
                })
            if self.messaging and self.messaging._peer_link_active(resolved):
                return web.json_response({
                    "status": "ok",
                    "connected": True,
                    "linked_peers": self.messaging.linked_peers(),
                })
            if self.messaging and self.messaging.active_link:
                if self._peers_equivalent(resolved, self.messaging.active_peer_hash):
                    return web.json_response({
                        "status": "ok",
                        "connected": True,
                        "linked_peers": self.messaging.linked_peers(),
                    })
            dedupe_key = f"{peer_ip or 'unknown'}:{resolved[:16]}"
            now = time.time()
            if now - self._reverse_connect_last.get(dedupe_key, 0) < 3.0:
                return web.json_response({"status": "ok", "connecting": True, "deduped": True})
            self._reverse_connect_last[dedupe_key] = now
            caller_from = (data.get("ip") or "").strip()
            if caller_from:
                from chatxz.core.lan_rns import register_udp_peer_ip
                register_udp_peer_ip(caller_from)
            print(
                f"[connect] Outbound-connect request from {caller_from or peer_ip or 'unknown'} "
                f"for {resolved[:16]}..."
            )
            asyncio.create_task(
                self._reverse_connect_task(
                    resolved, caller_from or peer_ip, peer_port, caller_ip, self.port
                )
            )
            return web.json_response({"status": "ok", "connecting": True})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_rns_interfaces_get(self, request):
        settings = self.load_settings()
        interfaces = normalize_interface_list(settings.get("rns_interfaces"))
        return web.json_response({
            "interfaces": self._interfaces_for_api(interfaces),
            "presets": {k: v["label"] for k, v in INTERFACE_PRESETS.items()},
            "restart_required": True,
        })

    async def handle_rns_interfaces_add(self, request):
        try:
            data = await request.json()
            preset = (data.get("preset") or "udp_lan").strip()
            settings = self.load_settings()
            settings["rns_interfaces"] = add_interface(settings.get("rns_interfaces"), preset)
            self.save_settings(settings)
            self._write_rns_config(settings)
            return web.json_response({
                "status": "ok",
                "interfaces": self._interfaces_for_api(settings["rns_interfaces"]),
                "message": "Interface added. Restart chatxz to apply.",
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_rns_interfaces_delete(self, request):
        try:
            data = await request.json()
            iface_id = (data.get("id") or "").strip()
            if not iface_id:
                return web.json_response({"error": "id required"}, status=400)
            settings = self.load_settings()
            settings["rns_interfaces"] = delete_interface(settings.get("rns_interfaces"), iface_id)
            self.save_settings(settings)
            self._write_rns_config(settings)
            return web.json_response({
                "status": "ok",
                "interfaces": settings["rns_interfaces"],
                "message": "Interface removed. Restart chatxz to apply.",
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_serial_ports_get(self, request):
        ports = await asyncio.to_thread(list_serial_ports)
        android = is_android()
        has_groups = None if android else user_has_serial_group_access()
        denied = [p for p in ports if p.get("status") == "permission_denied"]
        hint = serial_permission_hint_for_process() if denied else (
            ANDROID_SERIAL_PERMISSION_HINT if android else SERIAL_PERMISSION_HINT
        )
        return web.json_response({
            "ports": ports,
            "baud_rates": SERIAL_BAUD_RATES,
            "default_baud": SERIAL_DEFAULT_BAUD,
            "permission_hint": hint,
            "has_group_access": has_groups,
            "process_needs_restart": bool(denied and has_groups) if not android else False,
            "platform": "android" if android else "desktop",
            "can_request_usb_permission": android,
            "count": len(ports),
            "ready_count": sum(1 for p in ports if p.get("status") == "ok"),
        })

    async def handle_serial_usb_permission(self, request):
        if not is_android():
            return web.json_response({"error": "USB permission API is Android-only"}, status=400)
        try:
            data = await request.json()
            device = (data.get("device") or data.get("port") or "").strip()
            if not device:
                return web.json_response({"error": "device required"}, status=400)
            from usb4a import usb
            dev = usb.get_usb_device(device)
            if not dev:
                return web.json_response({"error": "device not found"}, status=404)
            if usb.has_usb_permission(dev):
                return web.json_response({"status": "ok", "granted": True})
            usb.request_usb_permission(dev)
            return web.json_response({"status": "ok", "granted": False, "requested": True})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_rns_interfaces_update(self, request):
        try:
            data = await request.json()
            iface_id = (data.get("id") or "").strip()
            if not iface_id:
                return web.json_response({"error": "id required"}, status=400)
            settings = self.load_settings()
            settings["rns_interfaces"] = update_interface(
                settings.get("rns_interfaces"),
                iface_id,
                data,
            )
            self.save_settings(settings)
            self._write_rns_config(settings)
            serial_hot = None
            serial_hot = await self._run_blocking(
                ensure_runtime_serial, settings.get("rns_interfaces")
            )
            if serial_hot and self.messaging:
                from chatxz.core.lan_rns import prune_stale_lan_paths
                await self._run_blocking(prune_stale_lan_paths)
                await self._run_blocking(self.messaging._silent_announce)
                peer = (
                    self.messaging.active_peer_hash
                    or getattr(self.messaging, "_session_peer_hash", None)
                )
                if peer:
                    await self._run_blocking(
                        self.messaging._prime_serial_path, peer, 12.0
                    )
            if serial_hot:
                msg = "Serial interface attached to RNS (no restart needed)."
            elif is_android():
                msg = "Settings saved. Select a USB port and grant access if needed."
            else:
                msg = "Interface updated."
            warning = None
            for iface in settings.get("rns_interfaces") or []:
                if iface.get("type") == "TCPClientInterface":
                    warning = tcp_client_target_warning(iface.get("target_host"))
                    if warning:
                        break
            return web.json_response({
                "status": "ok",
                "interfaces": self._interfaces_for_api(settings["rns_interfaces"]),
                "serial_hot_added": bool(serial_hot),
                "message": msg,
                "warning": warning,
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    def _beacon_payload(self):
        from chatxz.core.peer_identity import connect_hash_for_manager

        dest = ""
        if self.messaging and self.messaging.my_dest_hash:
            dest = self._clean_hash(self.messaging.my_dest_hash)
        elif self.messaging and self.messaging.destination:
            dest = self._clean_hash(RNS.hexrep(self.messaging.destination.hash))
        if not dest:
            dest = connect_hash_for_manager(
                self.identity_mgr,
                getattr(self.messaging, "destination", None) if self.messaging else None,
            )
        if not dest:
            dest = self._clean_hash(self.destination_hash or "")
        if not dest and self.identity_mgr:
            dest = self.identity_mgr.get_connect_hash()
        ident = self._clean_hash(self.identity_mgr.get_hex_hash() if self.identity_mgr else "")
        payload = {
            "app": "chatxz",
            "v": 1,
            "hash": dest,
            "name": self.load_settings().get("name", ""),
            "ip": detect_lan_ip() or "",
            "port": self.port,
        }
        if ident and ident != dest:
            payload["identity_hash"] = ident
        if self.identity:
            try:
                import base64
                payload["pubkey"] = base64.b64encode(
                    self.identity.get_public_key()
                ).decode("ascii")
            except Exception:
                pass
        return payload

    def _platform_name(self):
        if is_android() or self.embedded:
            return "android"
        return "desktop"

    def _reset_network_state(self, update_settings=True):
        if self.messaging:
            self.messaging.disconnect_all_peers(clear_session=True)
        self.active_peer = None
        if self.discovery:
            self.discovery.clear_peers()
            self.discovery.accept_peers = True
        if self.lan_beacon:
            self.lan_beacon.reset_stats()
        if update_settings:
            settings = self.load_settings()
            settings["network_stats_reset_at"] = time.time()
            self.save_settings(settings)

    def _maybe_auto_reset_network_stats(self):
        settings = self.load_settings()
        if not settings.get("network_stats_auto_reset", True):
            return
        last = float(settings.get("network_stats_reset_at") or 0)
        if last and (time.time() - last) < NETWORK_STATS_AUTO_RESET_SEC:
            return
        if self.lan_beacon:
            self.lan_beacon.reset_stats()
        settings["network_stats_reset_at"] = time.time()
        self.save_settings(settings)
        print("[network] Auto-reset beacon counters (weekly)")

    async def handle_network_reset(self, request):
        self._reset_network_state(update_settings=True)
        await self._broadcast({"type": "peers", "data": []})
        await self._broadcast({"type": "link_closed", "data": {"linked_peers": []}})
        await self._broadcast({"type": "network_reset", "data": {}})
        beacon = self.lan_beacon.status() if self.lan_beacon else None
        return web.json_response({
            "status": "ok",
            "beacon": beacon,
            "discovery_active": bool(self.discovery and self.discovery.accept_peers),
        })

    async def handle_network_repair(self, request):
        """Dedupe duplicate UDP/TCP LAN interfaces and rewrite RNS config."""
        try:
            settings = self.load_settings()
            raw = settings.get("rns_interfaces") or []
            before = len(raw)
            settings["rns_interfaces"] = normalize_interface_list(raw)
            after = len(settings["rns_interfaces"])
            self.save_settings(settings)
            self._write_rns_config(settings)
            return web.json_response({
                "status": "ok",
                "removed": max(0, before - after),
                "interfaces": self._interfaces_for_api(settings["rns_interfaces"]),
                "message": "Repaired LAN interfaces — restart chatxz to apply.",
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    def _disable_rns_serial_interfaces(self):
        try:
            settings = self.load_settings()
            port, _ = configured_serial_port(settings.get("rns_interfaces"))
            n = remove_serial_interfaces(port or None)
            if n:
                print(f"[serial] Removed {n} SerialInterface(s) after port unplug")
        except Exception as e:
            print(f"[serial] Could not remove runtime serial interface: {e}")

    async def _serial_watchdog_loop(self):
        serial_detach_sent = False
        serial_was_online = False
        while True:
            await asyncio.sleep(5)
            if self._shutting_down:
                return
            settings = self.load_settings()
            interfaces = normalize_interface_list(settings.get("rns_interfaces"))
            port, _ = configured_serial_port(interfaces)
            if not port:
                serial_was_online = False
                continue
            if serial_port_status(port) == "missing":
                serial_was_online = False
                if not serial_detach_sent:
                    self._disable_rns_serial_interfaces()
                    serial_detach_sent = True
            else:
                serial_detach_sent = False
                was_online = serial_was_online
                iface = await self._run_blocking(ensure_runtime_serial, interfaces)
                serial_was_online = iface is not None
                if serial_was_online and not was_online and self.messaging:
                    await self._run_blocking(self.messaging.on_serial_transport_attached, iface)

    def _peer_in_discovery(self, peer_hash, peer_ip=None):
        from chatxz.core.discovery import normalize_hash
        if not self.discovery:
            return None
        clean = normalize_hash(peer_hash)
        by_hash = None
        by_ip = None
        for p in self.discovery.get_peers():
            ph = normalize_hash(p.get("hash"))
            ih = normalize_hash(p.get("identity_hash"))
            if peer_ip and p.get("ip") == peer_ip:
                by_ip = p
            if clean and (
                ph == clean
                or ih == clean
                or self._peers_equivalent(ph, clean)
                or (ih and self._peers_equivalent(ih, clean))
            ):
                by_hash = p
        return by_hash or by_ip

    def _peer_connect_meta(self, peer_hash):
        peer_ip = None
        peer_port = 8742
        stored_ip, stored_port = contact_connect_meta(
            self.config_dir, peer_hash, self._peers_equivalent
        )
        if stored_ip:
            peer_ip = stored_ip
            peer_port = stored_port or peer_port
        peer = self._peer_in_discovery(peer_hash)
        if peer:
            if peer.get("ip"):
                peer_ip = peer.get("ip")
            peer_port = peer.get("port") or peer_port
        return peer_ip, peer_port

    def _resolve_peer_connect_ip(self, peer_hash, peer_ip=None, peer_port=8742):
        """Fill peer IP/port from discovery when the UI did not pass them (common on Android)."""
        if peer_ip:
            return peer_ip, peer_port
        resolved_ip, resolved_port = self._peer_connect_meta(peer_hash)
        if resolved_ip:
            return resolved_ip, resolved_port or peer_port
        return peer_ip, peer_port

    async def _resume_session_task(self, peer, peer_ip, peer_port):
        try:
            if self.messaging and self.messaging.is_user_disconnected(peer):
                return
            result = await self._run_blocking(
                self.messaging.resume_session_peer,
                peer_ip,
                peer_port,
                self._discovery_peer_for_connect,
                detect_lan_ip(),
                self.port,
            )
            if self._shutting_down or result is None:
                return
            if result:
                clean = self._peer_dest_hash(self.messaging.active_peer_hash or peer)
                self.active_peer = clean
                await self._broadcast({"type": "link_established", "data": {"hash": clean}})
                print(f"[connect] Session resumed with {clean[:16]}...")
        except Exception as e:
            print(f"[connect] Session resume error: {e}")

    async def _link_failover_loop(self):
        """Detect dead or migrated RNS paths and reconnect without server restart."""
        physical_lan_was_up = physical_lan_reachable()
        while not self._shutting_down:
            try:
                await asyncio.sleep(8)
            except asyncio.CancelledError:
                break
            if self._shutting_down or not self.messaging:
                continue
            from chatxz.core.lan_rns import (
                clear_paths_on_family,
                prune_stale_lan_paths,
                suppress_offline_lan_transports,
            )
            physical_lan_up = physical_lan_reachable()
            if physical_lan_up and not physical_lan_was_up and self.messaging:
                await self._run_blocking(self.messaging._silent_announce)
                self.messaging._transport_reconnect_pending = True
                self.messaging._failover_last_attempt = 0
                print("[network] LAN restored — refreshing paths and reconnecting")
            physical_lan_was_up = physical_lan_up
            await self._run_blocking(suppress_offline_lan_transports)
            await self._run_blocking(dedupe_serial_interfaces)
            if not serial_interface_online():
                await self._run_blocking(prune_dead_serial_interfaces)
                await self._run_blocking(clear_paths_on_family, "serial")
            await self._run_blocking(prune_stale_lan_paths)
            peer = self._peer_dest_hash(
                self.messaging.active_peer_hash
                or getattr(self.messaging, "_session_peer_hash", None)
                or self.active_peer
            )
            if not peer:
                continue

            needs, reason = self.messaging.session_needs_reconnect()
            if not needs:
                continue

            settings = self.load_settings()
            interfaces = normalize_interface_list(settings.get("rns_interfaces"))
            hub_role = settings.get("hub_role", "off")
            if hub_role != "off" and not lan_discovery_configured(interfaces):
                continue
            if configured_udp_lan_enabled(interfaces):
                await self._run_blocking(patch_udp_interface_unicast)
            if configured_tcp_lan_enabled(interfaces) and hub_role == "off":
                await self._run_blocking(ensure_runtime_tcp_lan_server, settings, self.config_dir)
            await self._run_blocking(ensure_runtime_serial, interfaces)

            peer_ip, peer_port = self._peer_connect_meta(peer)
            if not physical_lan_reachable() and configured_serial_enabled(interfaces):
                peer_ip = None
            if (
                configured_udp_lan_enabled(interfaces)
                and physical_lan_reachable()
                and self.lan_beacon
            ):
                await self._run_blocking(self.lan_beacon.send, 1, False)
            print(f"[connect] Failover triggered: {reason}")
            if self._shutting_down:
                continue

            result = await self._run_blocking(
                self.messaging.reconnect_active_peer,
                peer_ip,
                peer_port,
                self._discovery_peer_for_connect,
                detect_lan_ip(),
                self.port,
                reason,
            )
            if result:
                clean = self._peer_dest_hash(self.messaging.active_peer_hash or peer)
                self.active_peer = clean
                print(f"[connect] Failover complete with {clean[:16]}...")
            else:
                print(f"[connect] Failover attempt failed ({reason})")

    async def handle_network(self, request):
        """Alias for network-status — used by setup wizard and settings."""
        return await self.handle_network_status(request)

    async def handle_interfaces_get(self, request):
        refresh = request.query.get("refresh", "").lower() in ("1", "true", "yes")
        ifaces = await asyncio.to_thread(
            lambda: self._interfaces_for_picker(refresh=refresh)
        )
        return web.json_response({"interfaces": ifaces})

    async def handle_network_status(self, request):
        try:
            settings = self.load_settings()
            await self._run_blocking(
                ensure_runtime_serial, settings.get("rns_interfaces")
            )
        except Exception:
            pass
        rns_interfaces = []
        try:
            for iface in getattr(RNS.Transport, "interfaces", []) or []:
                rns_interfaces.append({
                    "type": type(iface).__name__,
                    "online": bool(getattr(iface, "online", False)),
                    "name": str(getattr(iface, "name", "") or getattr(iface, "interface_name", "")),
                })
        except Exception:
            pass
        peers = self._scoped_peers()
        linked_peers = self.messaging.linked_peers() if self.messaging else []
        link_active = False
        active_peer = None
        link_rns_interface = None
        if self.messaging:
            if self.messaging.active_link:
                try:
                    import RNS
                    healthy = self.messaging._link_interface_healthy(
                        self.messaging.active_link
                    )
                    link_active = (
                        healthy
                        and self.messaging.active_link.status == RNS.Link.ACTIVE
                    )
                except Exception:
                    link_active = False
                if link_active:
                    active_peer = self.active_peer or self.messaging.active_peer_hash
                    try:
                        iface = self.messaging._link_attached_interface(
                            self.messaging.active_link
                        )
                        if iface:
                            link_rns_interface = type(iface).__name__
                    except Exception:
                        pass
            if not link_active:
                for p in linked_peers:
                    if not self.messaging._peer_link_active(p):
                        continue
                    link = self.messaging._link_for_peer(p)
                    if link and self.messaging._link_interface_healthy(link):
                        link_active = True
                        active_peer = p
                        try:
                            iface = self.messaging._link_attached_interface(link)
                            if iface:
                                link_rns_interface = type(iface).__name__
                        except Exception:
                            pass
                        break
        port, _ = configured_serial_port(self.load_settings().get("rns_interfaces"))
        settings = self.load_settings()
        configured = settings.get("rns_interfaces")
        from chatxz.core.rns_interfaces import (
            tcp_client_interface_online,
            tcp_server_interface_online,
        )
        hub_role = settings.get("hub_role", "off")
        hub_port = int(settings.get("hub_port") or 4242)
        tcp_hub_online = bool(
            hub_role == "server" and tcp_server_interface_online(hub_port)
        )
        tcp_client_online = bool(
            hub_role == "client" and tcp_client_interface_online()
        )
        lan_discovery = lan_discovery_configured(configured)
        refresh_ifaces = request.query.get("refresh", "").lower() in ("1", "true", "yes")
        if lan_discovery and sys.platform in ("win32", "darwin"):
            lan_snap = await asyncio.to_thread(desktop_lan_status)
            lan_up = lan_snap["lan_connected"]
            lan_ip_value = lan_snap["lan_ip"] if lan_up else None
            bcast_value = lan_snap["broadcast"] if lan_up else None
        else:
            lan_up = lan_connected() if lan_discovery else False
            lan_ip_value = detect_lan_ip() if lan_up else None
            bcast_value = lan_broadcast() if lan_up else None
        avail_ifaces = await asyncio.to_thread(
            lambda: self._interfaces_for_picker(refresh=refresh_ifaces)
        )
        return web.json_response({
            "platform": self._platform_name(),
            "embedded": self.embedded,
            "app_version": APP_VERSION,
            "http_bind": f"{self.host}:{self.port}",
            "http_webview": f"127.0.0.1:{self.port}" if self.embedded else None,
            "discovery_active": bool(self.discovery and self.discovery.accept_peers),
            "rns_udp_port": 4242,
            "beacon_udp_port": BEACON_PORT,
            "lan_connected": lan_up,
            "lan_discovery_configured": lan_discovery,
            "serial_only_mode": (
                configured_serial_enabled(configured) and not lan_discovery
            ),
            "lan_ip": lan_ip_value if lan_discovery else (
                "not configured" if not lan_discovery else None
            ),
            "broadcast": bcast_value if lan_up else (
                "not configured" if not lan_discovery else None
            ),
            "interfaces": list_network_interfaces(),
            "available_interfaces": avail_ifaces,
            "lan_interface": get_lan_interface_preference() or "",
            "rns_ready": bool(self.messaging and self.messaging.destination),
            "rns_error": self.rns_init_error,
            "rns_interfaces": rns_interfaces,
            "configured_interfaces": self._interfaces_for_api(
                self.load_settings().get("rns_interfaces")
            ),
            "serial_group_access": (
                None if is_android() else user_has_serial_group_access()
            ),
            "usb_serial_ready": (
                sum(1 for p in list_serial_ports() if p.get("status") == "ok")
                if is_android() else None
            ),
            "beacon": self.lan_beacon.status() if self.lan_beacon else None,
            "discovered_peers": peers,
            "discovered_count": len(peers),
            "ws_clients": self._ws_client_count(),
            "link_active": link_active,
            "linked_peers": linked_peers,
            "active_peer": active_peer,
            "link_rns_interface": link_rns_interface,
            "serial_configured_port": port or None,
            "serial_in_rns": bool(port and serial_interface_online(port)),
            "session_peer": (
                getattr(self.messaging, "_session_peer_hash", None)
                if self.messaging else None
            ),
            "queue_size": self.messaging.queue_size() if self.messaging else 0,
            "debug_log_path": debug_log_path() if is_android() else None,
            "hub_role": hub_role,
            "hub_host": settings.get("hub_host") or "",
            "hub_port": hub_port,
            "hub_server_hash": settings.get("hub_server_hash") or "",
            "tcp_hub_online": tcp_hub_online,
            "tcp_client_online": tcp_client_online,
        })

    async def handle_path_wake(self, request):
        """Silent RNS path refresh for connect wake - no discovery or beacon."""
        ok, err = await self._wait_for_rns()
        if not ok:
            return web.json_response({"error": err or "not ready"}, status=400)
        try:
            await asyncio.to_thread(self.messaging._silent_announce)
            return web.json_response({"status": "ok"})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    ANNOUNCE_DEBOUNCE_SEC = 0.4

    async def _perform_announce(self):
        ok, err = await self._wait_for_rns()
        if not ok:
            return {"ok": False, "error": err or "not ready"}

        now = time.time()
        debounced = False
        beacon_sent = 0
        try:
            with self._announce_lock:
                if now - self._last_announce_at < self.ANNOUNCE_DEBOUNCE_SEC:
                    debounced = True
                else:
                    self._last_announce_at = now
                    self._enable_discovery(clear=False)
                    settings = self.load_settings()
                    configured = settings.get("rns_interfaces")
                    if configured_serial_enabled(configured) and not lan_discovery_configured(configured):
                        await asyncio.to_thread(self.messaging._burst_serial_announce)
                        print("[network] RNS announce burst on serial (no LAN beacon)")
                    else:
                        await asyncio.to_thread(self.messaging._silent_announce)
                    if (
                        lan_discovery_configured(configured)
                        and lan_ip_reachable()
                        and self.lan_beacon
                    ):
                        beacon_sent = await asyncio.to_thread(
                            self.lan_beacon.send, 1, is_android()
                        )
                    elif configured_serial_enabled(configured) and lan_discovery_configured(configured):
                        print("[network] RNS announce on serial + LAN paths")
                    elif not lan_discovery_configured(configured):
                        print("[network] No LAN transport configured — RNS announce on active paths only")
                    elif not lan_ip_reachable():
                        print("[network] LAN disconnected — RNS announce on serial/other paths only")
        except Exception as e:
            return {"ok": False, "error": str(e)}

        peers = self._scoped_peers()
        await self._broadcast({"type": "peers", "data": peers})
        if debounced and self.lan_beacon:
            beacon_sent = self.lan_beacon.last_announce_sent
        return {
            "ok": True,
            "debounced": debounced,
            "broadcast": lan_broadcast(),
            "beacon_port": BEACON_PORT,
            "beacon_sent": beacon_sent,
            "beacon_session_total": (
                self.lan_beacon.packets_sent if self.lan_beacon else 0
            ),
            "lan_ip": detect_lan_ip(),
            "discovered_count": len(peers),
        }

    async def handle_announce(self, request):
        result = await self._perform_announce()
        if not result.get("ok"):
            return web.json_response(
                {"error": result.get("error") or "not ready"}, status=400
            )
        return web.json_response({
            "status": "ok",
            "debounced": result.get("debounced", False),
            "broadcast": result.get("broadcast"),
            "beacon_port": result.get("beacon_port"),
            "beacon_sent": result.get("beacon_sent", 0),
            "beacon_session_total": result.get("beacon_session_total", 0),
            "lan_ip": result.get("lan_ip"),
            "discovered_count": result.get("discovered_count", 0),
        })

    async def handle_disconnect(self, request):
        peer = ""
        if request.can_read_body:
            try:
                data = await request.json()
                peer = (data.get("peer") or "").strip()
            except Exception:
                pass
        if not peer:
            peer = request.query.get("peer", "").strip()
        if not peer:
            peer = self._ui_state.get("viewing_peer") or self.active_peer or ""
        peer = self._peer_dest_hash(peer)
        if self.messaging and peer:
            self.messaging.disconnect_peer(peer, user_initiated=True)
        elif self.messaging:
            self.messaging.disconnect_all_peers(clear_session=True)
        if self.active_peer and peer and self._peers_equivalent(self.active_peer, peer):
            self.active_peer = None
        await self._broadcast({
            "type": "link_closed",
            "data": {
                "peer": peer,
                "linked_peers": (
                    self.messaging.linked_peers() if self.messaging else []
                ),
            },
        })
        return web.json_response({"status": "ok"})

    async def handle_settings_get(self, request):
        return web.json_response(self._apply_hub_settings(self.load_settings()))

    def _abs_path_hint(self):
        if sys.platform == "win32":
            return "C:\\Users\\you\\Downloads"
        return "/home/user/Downloads"

    def _normalize_received_dir(self, raw):
        path = (raw or "").strip()
        if not path:
            return None, "Path is empty"
        path = os.path.expanduser(path)
        if sys.platform == "win32":
            if re.match(r"^[A-Za-z]:[^\\/]", path):
                path = path[:2] + "\\" + path[2:]
            path = os.path.normpath(path.replace("/", "\\"))
        else:
            path = os.path.normpath(path)
        if not os.path.isabs(path):
            for base in (self.config_dir, os.path.expanduser("~"), os.getcwd()):
                if not base:
                    continue
                candidate = os.path.normpath(os.path.join(base, path))
                if os.path.isdir(candidate):
                    path = candidate
                    break
            else:
                hint = self._abs_path_hint()
                return None, f"Path must be absolute (e.g. {hint})"
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

        if sys.platform in ("win32", "darwin"):
            try:
                from chatxz.utils.folder_picker import pick_folder
                picked = pick_folder(start)
                if picked:
                    return os.path.normpath(picked)
            except Exception:
                pass
        if sys.platform == "darwin":
            picked = _pick_directory_tkinter(start)
            if picked:
                return os.path.normpath(picked)
        if sys.platform == "win32":
            picked = _pick_directory_tkinter(start)
            if picked:
                return os.path.normpath(picked)

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

            if sys.platform == "win32":
                picked = self._pick_directory_native()
            else:
                picked = await asyncio.to_thread(self._pick_directory_native)
            if not picked:
                return web.json_response({
                    "error": "cancelled",
                    "platform": self._platform_name(),
                }, status=400)
            path, err = self._normalize_received_dir(picked)
            if err:
                return web.json_response({"error": err}, status=400)
            return web.json_response({"path": path, "platform": self._platform_name()})
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
            if "network_stats_auto_reset" in data:
                settings["network_stats_auto_reset"] = bool(data["network_stats_auto_reset"])
            if "hub_role" in data:
                role = (data.get("hub_role") or "off").strip().lower()
                if role in ("off", "server", "client"):
                    settings["hub_role"] = role
            if "hub_host" in data:
                settings["hub_host"] = (data.get("hub_host") or "").strip()
            if "hub_port" in data and data.get("hub_port") is not None:
                try:
                    settings["hub_port"] = int(data["hub_port"])
                except (TypeError, ValueError):
                    pass
            if "hub_server_hash" in data:
                settings["hub_server_hash"] = (data.get("hub_server_hash") or "").strip()
            config_dirty = False
            if "lan_transport" in data:
                preset = (data.get("lan_transport") or "").strip()
                if preset in ("udp_lan", "tcp_lan"):
                    settings["rns_interfaces"] = set_primary_lan_transport(
                        settings.get("rns_interfaces"), preset
                    )
                    config_dirty = True
            if "lan_interface" in data:
                settings["lan_interface"] = (data.get("lan_interface") or "").strip()
                set_lan_interface_preference(settings["lan_interface"])
                config_dirty = True
            if "auto_interface_enabled" in data:
                settings["auto_interface_enabled"] = bool(data["auto_interface_enabled"])
                config_dirty = True
            if "auto_announce" in data:
                settings["auto_announce"] = bool(data["auto_announce"])
            if "setup_complete" in data:
                settings["setup_complete"] = bool(data["setup_complete"])
            hub_changed = any(
                k in data for k in ("hub_role", "hub_host", "hub_port")
            )
            if settings.get("hub_role") == "client" and not (settings.get("hub_host") or "").strip():
                return web.json_response(
                    {"error": "Hub host IP is required for client mode"},
                    status=400,
                )
            settings = self._apply_hub_settings(settings)
            self.save_settings(settings)
            setup_fast = bool(data.get("setup_complete"))
            if setup_fast:
                if self.messaging:
                    self.messaging.display_name = settings.get("name", "")
                if config_dirty or hub_changed:
                    asyncio.create_task(
                        asyncio.to_thread(self._write_rns_config, settings)
                    )
                if hub_changed:
                    asyncio.create_task(self._apply_hub_runtime(settings))
                if "auto_announce" in data:
                    self._apply_auto_announce_settings(settings)
                return web.json_response({"status": "ok", "settings": settings})
            if config_dirty or hub_changed:
                await asyncio.to_thread(self._write_rns_config, settings)
            if hub_changed:
                await asyncio.to_thread(self._apply_hub_runtime, settings)
            if "auto_announce" in data:
                self._apply_auto_announce_settings(settings)
            if self.messaging:
                self.messaging.display_name = settings.get("name", "")
            self._apply_received_dir(settings)
            self._apply_retention()
            self._save_history()
            return web.json_response({"status": "ok", "settings": settings})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _reload_identity_runtime(self, old_dest_hash="", old_identity_hash=""):
        from chatxz.core.discovery import normalize_hash

        old_dest = normalize_hash(old_dest_hash)
        old_ident = normalize_hash(old_identity_hash or old_dest_hash)
        my_dest_clean = ""
        my_ident_clean = ""

        if self.messaging and self.identity:
            dest = await asyncio.to_thread(self.messaging.rebind_identity, self.identity)
            my_hash = RNS.hexrep(dest.hash)
            my_dest_clean = my_hash.replace(":", "")
            self.messaging.my_dest_hash = my_dest_clean
            self.destination_hash = my_hash
        elif self.identity_mgr:
            my_ident_clean = (self.identity_mgr.get_hex_hash() or "").replace(":", "")

        if self.identity_mgr:
            my_ident_clean = (self.identity_mgr.get_hex_hash() or "").replace(":", "")

        if self.discovery:
            self.discovery.purge_hashes({old_dest, old_ident, my_dest_clean, my_ident_clean})
            self.discovery.clear_peers()
            self.discovery.accept_peers = True

        if self.lan_beacon and my_dest_clean:
            self.lan_beacon.dest_hash = my_dest_clean
            self.lan_beacon.identity_hash = my_ident_clean
            try:
                self.lan_beacon.identity_pubkey = (
                    self.identity.get_public_key() if self.identity else None
                )
            except Exception:
                self.lan_beacon.identity_pubkey = None
            self.lan_beacon.display_name = self.load_settings().get("name", "")

        self.active_peer = None
        if self.messaging:
            self.messaging.display_name = self.load_settings().get("name", "")

        if self.websockets and self._loop:
            await self._broadcast({
                "type": "identity_changed",
                "data": {
                    "hash": my_dest_clean or my_ident_clean,
                    "identity_hash": my_ident_clean,
                    "old_hash": old_dest,
                    "old_identity_hash": old_ident,
                },
            })
            if self.messaging:
                await self._perform_announce()
            peers = self._scoped_peers()
            await self._broadcast({"type": "peers", "data": peers})

        print(
            f"[identity] Live identity update: {old_dest[:16] or old_ident[:16]}... "
            f"-> {(my_dest_clean or my_ident_clean)[:16]}..."
        )

    async def handle_regenerate_identity(self, request):
        try:
            from chatxz.core.discovery import normalize_hash

            old_dest = normalize_hash(self.destination_hash or "")
            old_ident = normalize_hash(self.identity_mgr.get_hex_hash() if self.identity_mgr else "")
            self.identity = self.identity_mgr.regenerate()
            await self._reload_identity_runtime(old_dest, old_ident)
            new_dest = normalize_hash(self.destination_hash or "")
            new_ident = normalize_hash(self.identity_mgr.get_hex_hash())
            return web.json_response({
                "status": "ok",
                "old_hash": old_dest or old_ident,
                "new_hash": new_dest or new_ident,
                "identity_hash": new_ident,
                "live": bool(self.messaging),
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_restart(self, request):
        if is_android():
            settings = self.load_settings()
            self._write_rns_config(settings)
            await asyncio.to_thread(self._apply_hub_runtime, settings)
            return web.json_response({
                "status": "restarting",
                "android": True,
                "rns_reloaded": True,
            })
        import sys, os
        if getattr(sys, "frozen", False) and sys.platform == "win32":
            exe = sys.executable
            cwd = os.path.dirname(os.path.abspath(exe))

            def _win_restart():
                sys.stdout.flush()
                stop_stale_chatxz_servers(exclude_pid=os.getpid())
                flags = (
                    getattr(subprocess, "DETACHED_PROCESS", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                )
                subprocess.Popen(
                    [exe],
                    cwd=cwd,
                    close_fds=True,
                    creationflags=flags,
                )
                os._exit(0)

            print(f"[restart] Spawning new process: {exe}")
            asyncio.get_event_loop().call_later(0.5, _win_restart)
            return web.json_response({"status": "restarting"})
        def _source_restart():
            sys.stdout.flush()
            stop_stale_chatxz_servers(exclude_pid=os.getpid())
            args = [sys.executable, "-m", "chatxz.web.server", *sys.argv[1:]]
            env = os.environ.copy()
            root = os.environ.get("CHATXZ_ROOT") or os.getcwd()
            env["CHATXZ_ROOT"] = root
            env["PYTHONPATH"] = root
            subprocess.Popen(
                args,
                cwd=root,
                env=env,
                start_new_session=True,
            )
            os._exit(0)

        print("[restart] Spawning new server process")
        asyncio.get_event_loop().call_later(0.8, _source_restart)
        return web.json_response({"status": "restarting"})

    async def handle_temperature(self, request):
        try:
            avg = await asyncio.to_thread(get_avg_cpu_temperature)
        except Exception:
            avg = None
        return web.json_response({"avg_celsius": avg})

    async def handle_cpu(self, request):
        pct = await asyncio.to_thread(get_cpu_percent)
        if pct is not None:
            return web.json_response({"cpu_percent": pct})
        return web.json_response({"cpu_percent": None})

    async def handle_debug(self, request):
        peers = self._scoped_peers()
        settings = self.load_settings()
        received_dir = settings.get("received_dir", os.path.join(self.config_dir, "received"))
        payload = {
            "identity_hash": self.identity_mgr.get_hex_hash() if self.identity_mgr else None,
            "ws_clients": self._ws_client_count(),
            "discovered_peers": peers,
            "discovery_running": self.discovery.running if self.discovery else False,
            "discovery_active": bool(self.discovery and self.discovery.accept_peers),
            "lan_beacon_port": BEACON_PORT,
            "lan_beacon_running": bool(self.lan_beacon and self.lan_beacon.running),
            "lan_beacon_targets": self.lan_beacon.last_send_targets if self.lan_beacon else [],
            "lan_beacon_sent": self.lan_beacon.packets_sent if self.lan_beacon else 0,
            "lan_beacon_received": self.lan_beacon.packets_received if self.lan_beacon else 0,
            "active_peer": self.active_peer,
            "message_count": len(self.message_history),
            "loop_running": self._loop is not None and self._loop.is_running(),
            "rns_interfaces": len(RNS.Transport.interfaces) if hasattr(RNS.Transport, 'interfaces') else "unknown",
            "received_files_dir": received_dir,
            "settings": settings,
        }
        if is_android():
            payload["debug_log_path"] = debug_log_path()
            payload["debug_log_files"] = list_debug_log_files()
            tail = debug_log_tail()
            if tail:
                payload["debug_log_tail"] = tail
        return web.json_response(payload)

    async def handle_debug_export(self, request):
        if not is_android():
            return web.json_response(
                {"error": "Debug log export is for Android debug builds"},
                status=400,
            )
        try:
            data = await request.json()
            dest = (data.get("path") or "").strip()
            if not dest:
                return web.json_response({"error": "path required"}, status=400)
            copied, err = await asyncio.to_thread(export_debug_logs, dest)
            if err and copied == 0:
                return web.json_response({"error": err}, status=400)
            return web.json_response({
                "status": "ok",
                "copied": copied,
                "path": dest,
                "warning": err,
            })
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=500)

    async def handle_file_upload(self, request):
        if not self.messaging:
            return web.json_response({"error": "not ready"}, status=400)
        peer_hint = request.query.get("peer", "").strip()
        if peer_hint:
            self._ui_state["viewing_peer"] = self._peer_dest_hash(peer_hint)
        try:
            reader = await request.multipart()
            field = await reader.next()
            if not field:
                return web.json_response({"error": "no file"}, status=400)
            fname = safe_basename(field.filename, default=f"file_{int(time.time())}")
            msg_type = media_type_for_filename(fname)

            sent_dir = os.path.join(self.config_dir, "sent")
            os.makedirs(sent_dir, exist_ok=True)
            save_path = safe_path_under(sent_dir, fname)
            if not save_path:
                return web.json_response({"error": "invalid filename"}, status=400)
            size = 0
            with open(save_path, "wb") as f:
                while True:
                    chunk = await field.read_chunk(8192)
                    if not chunk:
                        break
                    f.write(chunk)
                    size += len(chunk)

            queue_target = self._queue_target_hash()
            transfer_id = str(uuid.uuid4())[:12]
            linked_to_target = bool(
                queue_target and self.messaging._peer_link_active(queue_target)
            )
            if not linked_to_target or self.messaging._has_active_transfer():
                self.messaging.enqueue(
                    msg_type, save_path,
                    target_hash=queue_target,
                    file_name=fname, file_size=size, file_path=save_path,
                    msg_id=transfer_id,
                )
                my_hash = self._my_sender_hash()
                chat_peer = self._peer_dest_hash(queue_target) or self._session_chat_peer()
                entry = self._enrich_message({
                    "type": msg_type,
                    "content": save_path,
                    "sender": my_hash,
                    "peer": chat_peer,
                    "chat_peer": chat_peer,
                    "timestamp": time.time(),
                    "file_name": fname,
                    "file_size": size,
                    "msg_id": transfer_id,
                    "status": "queued",
                }, outgoing=True)
                self.message_history.append(entry)
                self._save_history()
                await self._broadcast({"type": "message", "data": entry})
                return web.json_response({
                    "status": "queued",
                    "name": fname,
                    "size": size,
                    "msg_id": transfer_id,
                    "reason": None if not self.messaging.active_link else "transfer in progress",
                })
            my_hash = self._my_sender_hash()
            ts = time.time()
            chat_peer = self._peer_dest_hash(queue_target) or self._session_chat_peer()
            transfer_id = str(uuid.uuid4())[:12]
            entry = self._enrich_message({
                "type": msg_type,
                "content": save_path,
                "sender": my_hash,
                "peer": chat_peer,
                "chat_peer": chat_peer,
                "timestamp": ts,
                "file_name": fname,
                "file_size": size,
                "msg_id": transfer_id,
                "status": "sent",
            }, outgoing=True)
            self.message_history.append(entry)
            self._save_history()
            await self._broadcast({"type": "message", "data": entry})

            result = self.messaging.send_file(
                save_path, msg_type,
                progress_callback=self._make_progress_callback(fname, size, transfer_id),
                transfer_id=transfer_id,
                target_peer=queue_target,
            )
            if result:
                return web.json_response({"status": "ok", "name": fname, "size": size, "method": "resource"})
            return web.json_response({"error": "send failed"}, status=400)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_folder_upload(self, request):
        if not self.messaging:
            return web.json_response({"error": "not ready"}, status=400)
        peer_hint = request.query.get("peer", "").strip()
        if peer_hint:
            self._ui_state["viewing_peer"] = self._peer_dest_hash(peer_hint)
        try:
            folder_name = safe_basename(
                request.query.get("name", f"folder_{int(time.time())}"),
                default=f"folder_{int(time.time())}",
            )
            reader = await request.multipart()
            tmpdir = tempfile.mkdtemp(prefix="chatxz_folder_")
            total_size = 0
            file_count = 0
            while True:
                field = await reader.next()
                if not field:
                    break
                fpath = safe_rel_path_under(
                    tmpdir,
                    field.filename,
                    default_name=f"file_{file_count}",
                )
                if not fpath:
                    continue
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
            zip_entries = []
            for root, dirs, files in os.walk(tmpdir):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    zip_entries.append((fpath, os.path.relpath(fpath, tmpdir)))
            total_entries = len(zip_entries)
            await self._broadcast({"type": "progress", "data": {
                "stage": "zipping",
                "file_name": zip_name,
                "progress": 0,
                "direction": "send",
                "status": "active",
                "current": 0,
                "total": total_entries,
            }})
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for idx, (fpath, arcname) in enumerate(zip_entries):
                    zf.write(fpath, arcname)
                    pct = int(((idx + 1) / max(total_entries, 1)) * 100)
                    await self._broadcast({"type": "progress", "data": {
                        "stage": "zipping",
                        "file_name": zip_name,
                        "progress": pct,
                        "direction": "send",
                        "status": "active",
                        "current": idx + 1,
                        "total": total_entries,
                    }})
            shutil.rmtree(tmpdir, ignore_errors=True)
            zsize = os.path.getsize(zip_path)
            print(f"[folder] Created {zip_name} ({zsize} bytes, {file_count} files)")
            queue_target = self._queue_target_hash()
            linked_to_target = bool(
                queue_target and self.messaging._peer_link_active(queue_target)
            )
            if not linked_to_target or self.messaging._has_active_transfer():
                transfer_id = str(uuid.uuid4())[:12]
                self.messaging.enqueue(
                    "file", zip_path,
                    target_hash=queue_target,
                    file_name=zip_name, file_size=zsize, file_path=zip_path,
                    msg_id=transfer_id,
                )
                my_hash = self._my_sender_hash()
                chat_peer = self._peer_dest_hash(queue_target) or self._session_chat_peer()
                entry = self._enrich_message({
                    "type": "file",
                    "content": zip_path,
                    "sender": my_hash,
                    "peer": chat_peer,
                    "chat_peer": chat_peer,
                    "timestamp": time.time(),
                    "file_name": zip_name,
                    "file_size": zsize,
                    "msg_id": transfer_id,
                    "status": "queued",
                }, outgoing=True)
                self.message_history.append(entry)
                self._save_history()
                await self._broadcast({"type": "message", "data": entry})
                return web.json_response({
                    "status": "queued",
                    "name": zip_name,
                    "size": zsize,
                    "msg_id": transfer_id,
                    "reason": None if not linked_to_target else "transfer in progress",
                })
            my_hash = self._my_sender_hash()
            ts = time.time()
            chat_peer = self._peer_dest_hash(queue_target) or self._session_chat_peer()
            transfer_id = str(uuid.uuid4())[:12]
            entry = self._enrich_message({
                "type": "file",
                "content": zip_path,
                "sender": my_hash,
                "peer": chat_peer,
                "chat_peer": chat_peer,
                "timestamp": ts,
                "file_name": zip_name,
                "file_size": zsize,
                "msg_id": transfer_id,
                "status": "sent",
            }, outgoing=True)
            self.message_history.append(entry)
            self._save_history()
            await self._broadcast({"type": "message", "data": entry})
            result = self.messaging.send_file(
                zip_path, "file",
                progress_callback=self._make_progress_callback(zip_name, zsize, transfer_id),
                transfer_id=transfer_id,
                target_peer=queue_target,
            )
            if result:
                return web.json_response({"status": "ok", "name": zip_name, "size": zsize})
            return web.json_response({"error": "send failed"}, status=400)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    def _make_progress_callback(self, fname, total_size, transfer_id=None):
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
                    "transfer_id": transfer_id,
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
        file_name = data.get("file_name", "")
        cancelled = self.messaging.cancel_transfer(transfer_id, file_name=file_name)
        await self._broadcast({"type": "progress", "data": {
            "status": "cancelled",
            "progress": 0,
            "file_name": file_name,
            "transfer_id": transfer_id,
            "direction": "send",
        }})
        return web.json_response({"status": "ok" if cancelled else "noop"})

    async def handle_voice_upload(self, request):
        if not self.messaging:
            return web.json_response({"error": "not ready"}, status=400)
        try:
            data = await request.json()
            peer_hint = (data.get("peer") or "").strip()
            if peer_hint:
                self._ui_state["viewing_peer"] = self._peer_dest_hash(peer_hint)
            audio_b64 = data.get("audio", "")
            if not audio_b64:
                return web.json_response({"error": "no audio data"}, status=400)
            audio_bytes = base64.b64decode(audio_b64)
            sent_dir = os.path.join(self.config_dir, "sent")
            os.makedirs(sent_dir, exist_ok=True)
            voice_path = os.path.join(sent_dir, f"voice_{int(time.time())}.webm")
            with open(voice_path, "wb") as f:
                f.write(audio_bytes)

            queue_target = self._queue_target_hash()
            linked_to_target = bool(
                queue_target and self.messaging._peer_link_active(queue_target)
            )
            if not linked_to_target or self.messaging._has_active_transfer():
                voice_name = os.path.basename(voice_path)
                transfer_id = str(uuid.uuid4())[:12]
                self.messaging.enqueue(
                    "voice", voice_path, target_hash=queue_target,
                    file_name=voice_name,
                    file_size=len(audio_bytes), file_path=voice_path,
                    msg_id=transfer_id,
                )
                my_hash = self._my_sender_hash()
                chat_peer = self._peer_dest_hash(queue_target) or self._session_chat_peer()
                entry = self._enrich_message({
                    "type": "voice",
                    "content": voice_path,
                    "sender": my_hash,
                    "peer": chat_peer,
                    "chat_peer": chat_peer,
                    "timestamp": time.time(),
                    "file_name": voice_name,
                    "file_size": len(audio_bytes),
                    "msg_id": transfer_id,
                    "status": "queued",
                }, outgoing=True)
                self.message_history.append(entry)
                self._save_history()
                await self._broadcast({"type": "message", "data": entry})
                return web.json_response({
                    "status": "queued",
                    "msg_id": transfer_id,
                    "reason": None if not linked_to_target else "transfer in progress",
                })

            my_hash = self._my_sender_hash()
            ts = time.time()
            chat_peer = self._peer_dest_hash(queue_target) or self._session_chat_peer()
            voice_name = os.path.basename(voice_path)
            transfer_id = str(uuid.uuid4())[:12]
            entry = self._enrich_message({
                "type": "voice",
                "content": voice_path,
                "sender": my_hash,
                "peer": chat_peer,
                "chat_peer": chat_peer,
                "timestamp": ts,
                "file_name": voice_name,
                "file_size": len(audio_bytes),
                "msg_id": transfer_id,
                "status": "sent",
            }, outgoing=True)
            self.message_history.append(entry)
            self._save_history()
            await self._broadcast({"type": "message", "data": entry})

            result = self.messaging.send_file(
                voice_path, "voice",
                progress_callback=self._make_progress_callback(voice_name, len(audio_bytes), transfer_id),
                transfer_id=transfer_id,
                target_peer=queue_target,
            )
            if result:
                return web.json_response({"status": "ok"})
            return web.json_response({"error": "send failed"}, status=400)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_play_voice(self, request):
        try:
            data = await request.json()
            path = data.get("path", "")
            received_dir = self._received_dir()
            sent_dir = self._sent_dir()
            allowed = None
            if path:
                norm = os.path.normpath(path)
                if norm.startswith(received_dir + os.sep) or norm == received_dir:
                    allowed = norm
                elif norm.startswith(sent_dir + os.sep) or norm == sent_dir:
                    allowed = norm
            if allowed and os.path.isfile(allowed):
                VoicePlayer.play(allowed)
                return web.json_response({"status": "ok"})
            return web.json_response({"error": "file not found"}, status=404)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_serve_file(self, request):
        filepath = unquote(request.match_info["filepath"])
        received_dir = self._received_dir()
        sent_dir = self._sent_dir()
        if filepath.startswith("received/"):
            rel = "/".join(unquote(p) for p in filepath[9:].split("/"))
            full_path = os.path.normpath(os.path.join(received_dir, rel))
        elif filepath.startswith("sent/"):
            rel = "/".join(unquote(p) for p in filepath[5:].split("/"))
            full_path = os.path.normpath(os.path.join(sent_dir, rel))
        else:
            rel = "/".join(unquote(p) for p in filepath.split("/"))
            full_path = os.path.normpath(os.path.join(self.config_dir, rel))

        allowed = (
            full_path.startswith(received_dir + os.sep) or full_path == received_dir or
            full_path.startswith(sent_dir + os.sep) or full_path == sent_dir
        )
        if not allowed:
            return web.Response(text="Forbidden", status=403)
        if not os.path.exists(full_path) or not os.path.isfile(full_path):
            return web.Response(text="Not found", status=404)
        ct, _ = mimetypes.guess_type(full_path)
        if not ct:
            ext = os.path.splitext(full_path)[1].lower().lstrip(".")
            basename = os.path.basename(full_path)
            if ext == "webm" and basename.startswith("voice_"):
                ct = "audio/webm"
            else:
                ct = {
                    "webm": "video/webm",
                    "mp4": "video/mp4",
                    "m4v": "video/mp4",
                    "mkv": "video/x-matroska",
                    "mov": "video/quicktime",
                    "avi": "video/x-msvideo",
                    "ogv": "video/ogg",
                    "mpeg": "video/mpeg",
                    "mpg": "video/mpeg",
                }.get(ext)
        resp = web.FileResponse(full_path)
        if ct:
            resp.headers['Content-Type'] = ct
        if ct and ct.startswith("video/"):
            resp.headers['Accept-Ranges'] = 'bytes'
        return resp

    async def handle_queue(self, request):
        if not self.messaging:
            return web.json_response({"count": 0, "total": 0, "items": []})
        total = self.messaging.queue_size()
        peer = request.query.get("peer", "").strip()
        if peer:
            peer_clean = self._peer_dest_hash(peer)
            count = self.messaging.queue_size_for(peer_clean)
            items = [
                e for e in self.messaging.message_queue
                if self.messaging._queue_matches_target(e, peer_clean)
            ]
        else:
            count = total
            items = self.messaging.message_queue[-20:]
        return web.json_response({
            "count": count,
            "total": total,
            "items": items[-20:],
        })

    async def handle_queue_clear(self, request):
        cleared = 0
        if self.messaging:
            peer = None
            if request.can_read_body:
                try:
                    data = await request.json()
                    peer = (data.get("peer") or "").strip() or None
                except Exception:
                    pass
            if not peer:
                peer = request.query.get("peer", "").strip() or None
            before = self.messaging.queue_size()
            if peer:
                self.messaging.clear_queue(self._peer_dest_hash(peer))
            else:
                self.messaging.clear_queue()
            cleared = before - self.messaging.queue_size()
            if cleared:
                self.message_history = [
                    m for m in self.message_history if m.get("status") != "queued"
                ]
                self._save_history()
        await self._broadcast({"type": "queue_cleared", "data": {"count": cleared}})
        return web.json_response({"status": "ok", "cleared": cleared})

    def _clear_history_for_peer(self, peer_hash):
        peer = self._peer_dest_hash(peer_hash)
        if not peer:
            return 0
        before = len(self.message_history)
        self.message_history = [
            m for m in self.message_history
            if not self._peers_equivalent(m.get("chat_peer") or m.get("peer"), peer)
        ]
        self._save_history()
        return before - len(self.message_history)

    async def handle_history_clear(self, request):
        peer = request.query.get("peer", "").strip()
        if not peer and request.can_read_body:
            try:
                data = await request.json()
                peer = (data.get("peer") or "").strip()
            except Exception:
                pass
        if peer:
            removed = self._clear_history_for_peer(peer)
            peer_clean = self._peer_dest_hash(peer)
            await self._broadcast({
                "type": "peer_history_cleared",
                "data": {"peer": peer_clean, "removed": removed},
            })
            return web.json_response({"status": "ok", "peer": peer_clean, "removed": removed})
        self.message_history = []
        self._save_history()
        return web.json_response({"status": "ok", "removed": "all"})

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
        rows = [
            self._enrich_message(m)
            for m in self.message_history[-limit:]
            if not self._is_session_system_message(m)
        ]
        return web.json_response(rows)

    async def handle_websocket(self, request):
        self._prune_websockets()
        ws = web.WebSocketResponse(heartbeat=30.0)
        await ws.prepare(request)
        self.websockets.add(ws)
        print(f"[ws] Client connected ({self._ws_client_count()} total)")

        await self._send_peers_to(ws)
        if self.messaging:
            peer = self._peer_dest_hash(
                getattr(self.messaging, "_session_peer_hash", None) or self.active_peer
            )
            if (
                peer
                and not self.messaging.active_link
                and not self.messaging.is_user_disconnected(peer)
            ):
                now = time.time()
                if (
                    now - self._session_resume_last >= 45.0
                    and not getattr(self.messaging, "_failover_in_progress", False)
                    and (now - getattr(self.messaging, "_failover_last_attempt", 0))
                    >= getattr(self.messaging, "_failover_cooldown", lambda: 20.0)()
                ):
                    self._session_resume_last = now
                    peer_ip, peer_port = self._peer_connect_meta(peer)
                    asyncio.create_task(self._resume_session_task(peer, peer_ip, peer_port))

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
            print(f"[ws] Client disconnected ({self._ws_client_count()} total)")
        return ws

    async def _history_maintenance_loop(self):
        while True:
            await asyncio.sleep(60)
            if self._shutting_down:
                return
            self._prune_stale_session_system_messages()

    async def _discovery_broadcaster(self):
        print("[broadcaster] Started")
        last_snapshot = None
        while True:
            await asyncio.sleep(1)
            if not self.websockets or not self.discovery:
                continue
            peers = self._scoped_peers()
            snapshot = tuple(
                sorted(
                    (
                        (p.get("hash") or ""),
                        (p.get("identity_hash") or ""),
                        int(p.get("last_seen", 0)),
                    )
                    for p in peers
                )
            )
            self._prune_websockets()
            if snapshot != last_snapshot:
                count = len(peers)
                print(f"[broadcaster] {count} peer(s), {self._ws_client_count()} ws client(s)")
                last_snapshot = snapshot
                await self._broadcast({"type": "peers", "data": peers})

    async def handle_discover(self, request):
        peers = self._scoped_peers()
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
                peer_hint = data.get("peer") or data.get("hash") or ""
                if peer_hint:
                    self._ui_state["viewing_peer"] = self._peer_dest_hash(peer_hint)
                hub_send = peer_hint in (HUB_GROUP_PEER, "__hub_group__")
                settings = self.load_settings()
                hub_role = settings.get("hub_role", "off")
                if hub_send:
                    if hub_role == "off":
                        await ws.send_str(json.dumps({"type": "info", "data": "Hub mode is off - enable in Network settings"}))
                        return
                    def on_receipt(status, receipt):
                        if self._loop:
                            asyncio.run_coroutine_threadsafe(
                                self._broadcast({"type": "receipt", "data": {"msg_id": receipt.get("msg_id"), "status": status}}),
                                self._loop
                            )
                    result = self.messaging.send_hub_message(
                        text,
                        receipt_callback=on_receipt,
                        hub_server_hash=settings.get("hub_server_hash"),
                        hub_server_mode=(hub_role == "server"),
                    )
                    if result:
                        my_hash = self._my_sender_hash()
                        entry = self._enrich_message({
                            "type": result.msg_type,
                            "content": result.content,
                            "sender": my_hash,
                            "peer": HUB_GROUP_PEER,
                            "chat_peer": HUB_GROUP_PEER,
                            "timestamp": result.timestamp,
                            "msg_id": result.msg_id,
                            "hub_group": True,
                            "status": "sent",
                        }, outgoing=True)
                        self.message_history.append(entry)
                        self._save_history()
                        if self.debug:
                            print(f"[chat] send hub msg_id={entry['msg_id'][:8]}")
                        await self._broadcast({"type": "message", "data": entry})
                    else:
                        msg_id = str(uuid.uuid4())[:12]
                        self.messaging.enqueue("text", text, target_hash=HUB_GROUP_PEER, msg_id=msg_id)
                        my_hash = self._my_sender_hash()
                        entry = self._enrich_message({
                            "type": "text",
                            "content": text,
                            "sender": my_hash,
                            "peer": HUB_GROUP_PEER,
                            "chat_peer": HUB_GROUP_PEER,
                            "timestamp": time.time(),
                            "msg_id": msg_id,
                            "hub_group": True,
                            "status": "queued",
                        }, outgoing=True)
                        self.message_history.append(entry)
                        self._save_history()
                        await self._broadcast({"type": "message", "data": entry})
                        qsize = self.messaging.queue_size()
                        await ws.send_str(json.dumps({"type": "info", "data": f"Message queued ({qsize} pending)"}))
                    return
                target_hash = self._peer_dest_hash(peer_hint) if peer_hint else (
                    self._queue_target_hash()
                )
                if not target_hash and self.messaging._session_peer_hash:
                    target_hash = self.messaging._session_peer_hash
                if target_hash:
                    peer_ip = None
                    meta = self._discovery_peer_for_connect(None, target_hash)
                    if meta:
                        peer_ip = meta.get("ip")
                    target_hash = self._resolve_current_peer_hash(target_hash, peer_ip)
                    if self.discovery and not self._peer_is_current(target_hash):
                        await ws.send_str(json.dumps({
                            "type": "info",
                            "data": "Stale peer hash — open the peer from Discovered",
                        }))
                        return
                linked_to_target = bool(
                    target_hash and self.messaging.peer_send_ready(target_hash)
                )
                if linked_to_target:
                    def on_receipt(status, receipt):
                        if self._loop:
                            asyncio.run_coroutine_threadsafe(
                                self._broadcast({"type": "receipt", "data": {"msg_id": receipt.get("msg_id"), "status": status}}),
                                self._loop
                            )
                    result = self.messaging.send_message(
                        text, receipt_callback=on_receipt, target_peer=target_hash,
                    )
                    if result:
                        my_hash = self._my_sender_hash()
                        chat_peer = target_hash or self._session_chat_peer() or self._peer_dest_hash(self.active_peer)
                        entry = self._enrich_message({
                            "type": result.msg_type,
                            "content": result.content,
                            "sender": my_hash,
                            "peer": chat_peer,
                            "chat_peer": chat_peer,
                            "timestamp": result.timestamp,
                            "msg_id": result.msg_id,
                            "status": "sent",
                        }, outgoing=True)
                        self.message_history.append(entry)
                        self._save_history()
                        if self.debug:
                            print(f"[chat] send type={entry['type']} peer={chat_peer[:16]} msg_id={entry['msg_id'][:8]}")
                        await self._broadcast({"type": "message", "data": entry})
                else:
                    msg_id = str(uuid.uuid4())[:12]
                    self.messaging.enqueue("text", text, target_hash=target_hash, msg_id=msg_id)
                    my_hash = self._my_sender_hash()
                    chat_peer = target_hash or self._session_chat_peer() or self._peer_dest_hash(self.active_peer)
                    entry = self._enrich_message({
                        "type": "text",
                        "content": text,
                        "sender": my_hash,
                        "peer": chat_peer,
                        "chat_peer": chat_peer,
                        "timestamp": time.time(),
                        "msg_id": msg_id,
                        "status": "queued",
                    }, outgoing=True)
                    self.message_history.append(entry)
                    self._save_history()
                    await self._broadcast({"type": "message", "data": entry})
                    qsize = self.messaging.queue_size()
                    await ws.send_str(json.dumps({"type": "info", "data": f"Message queued ({qsize} pending)"}))
        elif msg_type == "connect":
            peer_hash = data.get("hash", "")
            if peer_hash and self.messaging:
                peer_ip = (data.get("ip") or "").strip() or None
                peer_port = data.get("port") or 8742
                resolved_hash = self._resolve_connect_target(peer_hash, peer_ip)
                peer_ip, peer_port = self._resolve_peer_connect_ip(resolved_hash, peer_ip, peer_port)
                caller_ip = detect_lan_ip() or (self.host if self.host not in ("127.0.0.1", "0.0.0.0") else "")
                ok = await self._run_blocking(
                    self.messaging.connect_to,
                    resolved_hash,
                    peer_ip,
                    peer_port,
                    self._discovery_peer_for_connect,
                    caller_ip,
                    self.port,
                    False,
                    False,
                    False,
                    True,
                )
                if self._shutting_down or ok is None:
                    await ws.send_str(json.dumps({"type": "connect_fail", "error": "server shutting down"}))
                elif ok:
                    clean = self._peer_dest_hash(resolved_hash)
                    self.active_peer = clean
                    await ws.send_str(json.dumps({
                        "type": "connect_ok",
                        "hash": clean,
                        "linked_peers": self.messaging.linked_peers(),
                    }))
                else:
                    await ws.send_str(json.dumps({"type": "connect_fail", "error": "connection failed"}))
        elif msg_type == "viewing":
            peer = data.get("peer") or ""
            self._ui_state["viewing_peer"] = self._peer_dest_hash(peer) if peer else None
        elif msg_type == "visibility":
            self._ui_state["hidden"] = bool(data.get("hidden"))
        elif msg_type == "announce":
            result = await self._perform_announce()
            if result.get("ok"):
                await ws.send_str(json.dumps({
                    "type": "announce_ok",
                    "debounced": result.get("debounced", False),
                    "discovered_count": result.get("discovered_count", 0),
                    "beacon_sent": result.get("beacon_sent", 0),
                }))
            else:
                err = result.get("error") or "not ready"
                await ws.send_str(json.dumps({"type": "info", "data": "Announce failed: " + err}))
        elif msg_type == "read_receipt":
            msg_id = data.get("msg_id", "")
            if msg_id and self.messaging:
                target = self._queue_target_hash() or self._peer_dest_hash(self.active_peer)
                link = self.messaging._link_for_peer(target) if target else None
                link = link or self.messaging.active_link
                if link:
                    self.messaging.send_read_receipt(link, msg_id)

    async def _init_rns_background(self):
        try:
            my_hash = await asyncio.to_thread(self.start_rns)
            print(f"[startup] RNS ready, identity: {my_hash}")
            await self._broadcast({"type": "rns_ready", "data": {"hash": my_hash}})
        except (SystemExit, RuntimeError) as e:
            self.rns_init_error = str(e) or "RNS startup failed"
            print(f"[startup] RNS init failed: {self.rns_init_error}")
            await self._broadcast({
                "type": "info",
                "data": f"Network stack failed: {self.rns_init_error}",
            })
        except Exception:
            import traceback
            self.rns_init_error = traceback.format_exc()
            print(f"[startup] RNS init failed:\n{self.rns_init_error}")

    async def _on_startup(self, app):
        self._loop = asyncio.get_running_loop()
        self._reset_connection_state()
        self._maybe_auto_reset_network_stats()
        print(f"[startup] Event loop captured: {self._loop}")
        for coro in (
            self._discovery_broadcaster(),
            self._history_maintenance_loop(),
            self._link_failover_loop(),
            self._serial_watchdog_loop(),
            self._queue_retry_loop(),
        ):
            task = asyncio.create_task(coro)
            self._background_tasks.append(task)
        if not self.embedded and not is_android():
            task = asyncio.create_task(self._init_rns_background())
            self._background_tasks.append(task)
        self._prune_stale_session_system_messages()
        retention = self.load_settings().get("history_retention", "never")
        if retention == "on_restart":
            self.message_history = []
            self._save_history()
            print("[history] Cleared on restart")

    async def _queue_retry_loop(self):
        while not self._shutting_down:
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            if self._shutting_down or not self.messaging:
                continue
            if not self.messaging.message_queue or not self.messaging.peer_links:
                continue
            try:
                sent = await asyncio.to_thread(self.messaging.retry_queue)
                if sent and self.websockets:
                    await self._broadcast({
                        "type": "queue_drained",
                        "data": {"sent": sent, "remaining": self.messaging.queue_size()},
                    })
            except Exception as e:
                print(f"[queue] Server retry error: {e}")

    def _register_routes(self, app):
        app.router.add_get("/", self.handle_index)
        app.router.add_get("/static/{filename:.*}", self.handle_static)
        app.router.add_get("/api/identity", self.handle_identity)
        app.router.add_post("/api/contacts", self.handle_add_contact)
        app.router.add_delete("/api/contacts/{hash}", self.handle_delete_contact)
        app.router.add_post("/api/connect", self.handle_connect)
        app.router.add_post("/api/request_connect", self.handle_request_connect)
        app.router.add_get("/api/rns-interfaces", self.handle_rns_interfaces_get)
        app.router.add_post("/api/rns-interfaces/add", self.handle_rns_interfaces_add)
        app.router.add_post("/api/rns-interfaces/delete", self.handle_rns_interfaces_delete)
        app.router.add_post("/api/rns-interfaces/update", self.handle_rns_interfaces_update)
        app.router.add_get("/api/serial-ports", self.handle_serial_ports_get)
        app.router.add_post("/api/serial-ports/permission", self.handle_serial_usb_permission)
        app.router.add_post("/api/announce", self.handle_announce)
        app.router.add_post("/api/path_wake", self.handle_path_wake)
        app.router.add_get("/api/network-status", self.handle_network_status)
        app.router.add_get("/api/network", self.handle_network)
        app.router.add_get("/api/interfaces", self.handle_interfaces_get)
        app.router.add_post("/api/network/reset", self.handle_network_reset)
        app.router.add_post("/api/network/repair", self.handle_network_repair)
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
        app.router.add_post("/api/debug/export", self.handle_debug_export)
        app.router.add_get("/api/settings", self.handle_settings_get)
        app.router.add_post("/api/settings", self.handle_settings_post)
        app.router.add_get("/api/browse-dir", self.handle_browse_dir)
        app.router.add_post("/api/browse-dir", self.handle_browse_dir)
        app.router.add_post("/api/transfer/cancel", self.handle_transfer_cancel)
        app.router.add_get("/api/file/{filepath:.*}", self.handle_serve_file)
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
            self._maybe_auto_reset_network_stats()
            for coro in (
                self._discovery_broadcaster(),
                self._embedded_init_rns(app),
                self._queue_retry_loop(),
                self._link_failover_loop(),
            ):
                task = asyncio.create_task(coro)
                self._background_tasks.append(task)
            retention = self.load_settings().get("history_retention", "never")
            if retention == "on_restart":
                self.message_history = []
                self._save_history()

        app.on_startup.append(_embedded_startup)
        app.on_shutdown.append(self._on_shutdown)
        app.on_cleanup.append(self._on_cleanup)
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
        from aiohttp.web_runner import GracefulExit

        if not is_android():
            holders = _port_holder_pids(self.port, udp=False)
            stale = [
                p for p in holders
                if p != os.getpid() and _is_chatxz_process(p)
            ]
            if stale or (self.force and holders):
                stop_stale_chatxz_servers(exclude_pid=os.getpid())
                time.sleep(0.4)

        app = web.Application()
        self._register_routes(app)
        app.on_startup.append(self._on_startup)
        app.on_shutdown.append(self._on_shutdown)
        app.on_cleanup.append(self._on_cleanup)

        print(f"chatxz web server v{APP_VERSION}")
        print(f"Web interface: http://{self.host}:{self.port}")
        print("[startup] HTTP listening — RNS/network stack starting in background")
        print("Press Ctrl+C to stop")

        try:
            web.run_app(app, host=self.host, port=self.port, print=lambda _: None)
        except GracefulExit:
            pass


def main():
    import argparse
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(line_buffering=True)
            sys.stderr.reconfigure(line_buffering=True)
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="chatxz web server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address")
    parser.add_argument("--port", type=int, default=8742, help="Port")
    parser.add_argument("--share", action="store_true", help="Listen on 0.0.0.0 (accessible on LAN)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show RNS debug logs")
    parser.add_argument("--debug", "-d", action="store_true",
                        help="Extreme RNS logging + chatxz trace logs (very noisy)")
    parser.add_argument("--force", "-f", action="store_true",
                        help="Stop any existing chatxz server before starting")
    args = parser.parse_args()
    host = "0.0.0.0" if args.share else args.host
    server = ChatWebServer(host=host, port=args.port, verbose=args.verbose, debug=args.debug, force=args.force)
    server.run()


if __name__ == "__main__":
    main()
