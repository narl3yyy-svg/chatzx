import os, json, time, base64, mimetypes, asyncio, socket, zipfile, shutil, subprocess, tempfile, signal, re, sys, threading, uuid
from urllib.parse import quote, unquote
from pathlib import Path

from aiohttp import web
import RNS

from chatxz.core.identity import IdentityManager
from chatxz.core.messaging import MessagingBackend
from chatxz.core.voice import VoiceRecorder, VoicePlayer
from chatxz.core.discovery import PeerDiscovery
from chatxz.core.lan_beacon import LanBeacon, BEACON_PORT
from chatxz.core.contacts import (
    contact_connect_meta,
    delete_contact as delete_saved_contact,
    list_contacts,
    save_contact,
)
from chatxz.core.lan_rns import patch_udp_interface_unicast, serial_interface_online
from chatxz.core.rns_interfaces import (
    INTERFACE_PRESETS,
    SERIAL_BAUD_RATES,
    SERIAL_DEFAULT_BAUD,
    ANDROID_SERIAL_PERMISSION_HINT,
    SERIAL_PERMISSION_HINT,
    serial_permission_hint_for_process,
    add_interface,
    configured_serial_port,
    delete_interface,
    ensure_runtime_serial,
    remove_serial_interfaces,
    list_serial_ports,
    normalize_interface_list,
    render_rns_config,
    serial_port_accessible,
    serial_port_status,
    serial_runtime_active,
    update_interface,
    user_has_serial_group_access,
)
from chatxz.utils.helpers import get_config_dir, get_data_dir, format_speed, media_type_for_filename
from chatxz.utils.debug_log import debug_log_path
from chatxz.utils.platform import (
    is_android,
    lan_ip as platform_lan_ip,
    lan_broadcast,
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


def _is_chatxz_process(pid):
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
    print("[startup] Stop it with:  pkill -f chatxz.web.server")
    print("[startup] Or restart with:  ./run.sh web --share --force")
    return False

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

        self.active_peer = None
        self.destination_hash = None
        self.discovery = None
        self.lan_beacon = None
        self._loop = None
        self.rns_init_error = None
        self._announce_lock = threading.Lock()
        self._reverse_connect_last = {}
        self._session_resume_last = 0.0
        self._shutting_down = False
        self._progress_last = {}
        self._progress_throttle_ms = 250

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

    async def _on_cleanup(self, app):
        self._shutting_down = True
        if self.messaging:
            self.messaging.shutdown_requested = True
            self.messaging.running = False
            try:
                self.messaging._teardown_active_link()
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

    def _peer_dest_hash(self, any_hash):
        if self.messaging:
            return self.messaging.dest_hash_for(any_hash)
        return self._clean_hash(any_hash).lower()

    def _my_sender_hash(self):
        return self._clean_hash(self.destination_hash or self.identity_mgr.get_hex_hash())

    def _is_self_hash(self, h):
        from chatxz.core.discovery import normalize_hash
        clean = normalize_hash(h)
        if not clean:
            return False
        my_ident = normalize_hash(self.identity_mgr.get_hex_hash() if self.identity_mgr else "")
        my_dest = normalize_hash(self.destination_hash or "")
        return clean in (my_dest, my_ident)

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
        if self.messaging and self.messaging.active_peer_hash:
            resolved = self._peer_dest_hash(self.messaging.active_peer_hash)
            if resolved and resolved != "unknown":
                return resolved
        if self.active_peer:
            resolved = self._peer_dest_hash(self.active_peer)
            if resolved and resolved != "unknown":
                return resolved
        if sender_hash:
            return self._peer_dest_hash(sender_hash)
        return ""

    def _resolve_incoming_peer(self, ident_hex=None, computed_dest=None, fallback=None, link=None):
        from chatxz.core.discovery import normalize_hash

        if computed_dest and not self._is_self_hash(computed_dest):
            return self._peer_dest_hash(computed_dest)

        clean_fallback = normalize_hash(fallback)
        if clean_fallback and not self._is_self_hash(clean_fallback):
            return self._peer_dest_hash(clean_fallback)

        if self.active_peer and ident_hex and self._peers_equivalent(ident_hex, self.active_peer):
            return self._peer_dest_hash(self.active_peer)

        if self.active_peer and computed_dest and self._peers_equivalent(computed_dest, self.active_peer):
            return self._peer_dest_hash(self.active_peer)

        if ident_hex and not self._is_self_hash(ident_hex) and self.discovery:
            for p in self.discovery.get_peers():
                ph = normalize_hash(p.get("hash"))
                ih = normalize_hash(p.get("identity_hash"))
                if ident_hex == ih or ident_hex == ph:
                    return ph or ident_hex

        if self.discovery:
            candidates = []
            for p in self.discovery.get_peers():
                ph = normalize_hash(p.get("hash"))
                if not ph or self._is_self_hash(ph):
                    continue
                candidates.append((p.get("last_seen", 0), ph, p.get("via")))
            if candidates:
                candidates.sort(key=lambda row: (10 if row[2] == "rns" else 0, row[0]), reverse=True)
                return candidates[0][1]

        if ident_hex and not self._is_self_hash(ident_hex):
            return self._peer_dest_hash(computed_dest or ident_hex)
        if self.active_peer:
            return self._peer_dest_hash(self.active_peer)
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

    def load_settings(self):
        try:
            with open(SETTINGS_FILE) as f:
                s = json.load(f)
                s.setdefault("name", "")
                s.setdefault("history_retention", "never")
                s.setdefault("received_dir", os.path.join(self.config_dir, "received"))
                s.setdefault("network_stats_auto_reset", True)
                s.setdefault("network_stats_reset_at", 0)
                s.setdefault("rns_interfaces", normalize_interface_list(None))
                return s
        except:
            return {"name": "", "history_retention": "never",
                    "received_dir": os.path.join(self.config_dir, "received"),
                    "network_stats_auto_reset": True, "network_stats_reset_at": 0,
                    "rns_interfaces": normalize_interface_list(None)}

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
        config_text = render_rns_config(interfaces, broadcast_ip=bcast, android=is_android())
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
        if self.embedded or is_android():
            patch_embedded_signals()
        settings = self.load_settings()
        self._write_rns_config(settings)
        self._log_serial_diagnostics()

        if not ensure_rns_ports_free(force=self.force):
            msg = "UDP port 4242 is already in use"
            if self.embedded:
                raise RuntimeError(msg)
            sys.exit(1)

        if self.debug:
            loglevel = getattr(RNS, "LOG_EXTREME", RNS.LOG_DEBUG)
        elif self.verbose:
            loglevel = RNS.LOG_DEBUG
        else:
            loglevel = RNS.LOG_NOTICE
        try:
            RNS.Reticulum(self.config_dir, loglevel=loglevel)
        except OSError as e:
            err = str(e)
            if "reinitialise" in err and self.messaging and self.messaging.destination:
                print("[RNS] Already running — reusing existing instance")
                return RNS.hexrep(self.messaging.destination.hash)
            print(f"[RNS] Bind error: {e}")
            if is_android():
                raise RuntimeError(f"RNS failed to start: {e}") from e
            print("[RNS] Retrying after stopping stale instances...")
            stop_stale_chatxz_servers()
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
        patch_udp_interface_unicast()
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
            on_link_established=self._on_link_established,
            on_link_closed=self._on_link_closed,
            display_name=settings.get("name", ""),
            auto_announce=is_android(),
            receive_dir=received_dir,
            peer_resolver=self._resolve_incoming_peer,
        )
        self.voice_recorder = VoiceRecorder(self.config_dir)
        dest = self.messaging.start()

        my_hash = RNS.hexrep(dest.hash)
        my_dest_clean = my_hash.replace(":", "")
        self.messaging.my_dest_hash = my_dest_clean
        self.destination_hash = my_hash
        self.discovery = PeerDiscovery(on_peer_seen=self._on_peer_discovered)
        self.discovery.start()
        identity_pubkey = None
        if self.identity:
            try:
                identity_pubkey = self.identity.get_public_key()
            except Exception:
                identity_pubkey = None
        android = is_android()
        self.lan_beacon = LanBeacon(
            self.discovery,
            my_dest_clean,
            display_name=settings.get("name", ""),
            ip=my_ip,
            port=self.port,
            periodic=android,
            identity_hash=self.identity_mgr.get_hex_hash(),
            identity_pubkey=identity_pubkey,
            on_periodic=(self.messaging.announce if android else None),
        )
        self.lan_beacon.start()
        if android:
            print("[network] Android: periodic beacon + RNS announce enabled")
        else:
            print("[network] Manual announce only — use Announce button or peer connect wake")

        serial_hot = ensure_runtime_serial(settings.get("rns_interfaces"))
        if serial_hot:
            print(f"[serial] Runtime serial interface active on {getattr(serial_hot, 'port', '?')}")

        return my_hash

    def _on_message(self, chat_msg, sender_hash):
        session_peer = self._session_chat_peer(sender_hash)
        if sender_hash and sender_hash != "system":
            resolved = self._peer_dest_hash(sender_hash)
            if session_peer and (
                self._is_self_hash(resolved)
                or self._peers_equivalent(resolved, session_peer)
            ):
                chat_peer = session_peer
            else:
                chat_peer = resolved
            sender = chat_peer
        else:
            chat_peer = session_peer or self._peer_dest_hash(self.active_peer)
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

    async def _broadcast(self, data):
        msg = json.dumps(data)
        for ws in self.websockets.copy():
            try:
                await ws.send_str(msg)
            except:
                self.websockets.discard(ws)

    def _on_peer_discovered(self, peer):
        if not self.messaging:
            return
        dest = self._peer_dest_hash(peer.get("hash"))
        if peer.get("identity_hash"):
            self.messaging.register_peer_mapping(dest, peer.get("identity_hash"))
        if peer.get("ip"):
            for contact in list_contacts(self.config_dir):
                if self._peers_equivalent(contact.get("hash"), dest):
                    if contact.get("ip") != peer.get("ip"):
                        save_contact(
                            self.config_dir,
                            contact.get("hash"),
                            ip=peer.get("ip"),
                            port=peer.get("port"),
                            identity_hash=peer.get("identity_hash"),
                        )
                    break
        if self.discovery and self.websockets and self._loop:
            peers = self.discovery.get_peers()
            asyncio.run_coroutine_threadsafe(
                self._broadcast({"type": "peers", "data": peers}),
                self._loop
            )

    def _on_link_closed(self, peer_hash, handoff=False):
        if handoff or getattr(self.messaging, "_failover_in_progress", False):
            return
        if self.messaging and self.messaging.active_link:
            return
        if self.websockets and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._broadcast({"type": "link_closed", "data": {}}),
                self._loop
            )

    def _on_link_established(self, peer_hash, link):
        resolved = self._peer_dest_hash(peer_hash)
        if self._is_self_hash(resolved) and self.discovery:
            fixed = self._resolve_incoming_peer(link=link)
            if fixed and not self._is_self_hash(fixed):
                resolved = fixed
        self.active_peer = resolved
        self._prune_stale_session_system_messages()
        path_switch = bool(getattr(self.messaging, "_last_handoff", False))
        print(f"[connect] Session active with {self.active_peer}" + (" (path switch)" if path_switch else ""))
        if self.websockets and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._broadcast({
                    "type": "link_established",
                    "data": {
                        "hash": self.active_peer,
                        "aliases": self._peer_alias_list(self.active_peer),
                        "path_switch": path_switch,
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
        h = normalize_hash(self.destination_hash or self.identity_mgr.get_hex_hash())
        contacts = list_contacts(self.config_dir)
        discovered = self.discovery.get_peers() if self.discovery else []
        link_active = bool(self.messaging and self.messaging.active_link)
        connected = self.active_peer if link_active and self.active_peer else None
        return web.json_response({
            "hash": h,
            "connected": connected,
            "contacts": contacts,
            "discovered": discovered,
            "platform": "android" if is_android() else "desktop",
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
        by_ip = None
        for p in self.discovery.get_peers():
            if peer_ip and p.get("ip") == peer_ip:
                by_ip = p
            if clean and normalize_hash(p.get("hash")) == clean:
                if p.get("via") == "rns":
                    return p
        return by_ip

    def _resolve_connect_target(self, peer_hash, peer_ip=None):
        from chatxz.core.discovery import normalize_hash
        resolved = self._resolve_peer_hash(peer_hash)
        if not self.discovery:
            return resolved
        if peer_ip:
            for p in self.discovery.get_peers():
                if p.get("ip") == peer_ip:
                    ph = self._resolve_peer_hash(p.get("hash"))
                    if p.get("via") == "rns":
                        return ph
                    return ph or resolved
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
            resolved_hash = self._resolve_connect_target(peer_hash, peer_ip)
            peer_ip, peer_port = self._resolve_peer_connect_ip(resolved_hash, peer_ip, peer_port)
            caller_ip = detect_lan_ip() or (self.host if self.host not in ("127.0.0.1", "0.0.0.0") else "")
            if is_android() and not caller_ip:
                print("[connect] Warning: could not detect Android LAN IP — reverse connect may fail")
            ok = await self._run_blocking(
                self.messaging.connect_to,
                resolved_hash,
                peer_ip,
                peer_port,
                self._discovery_peer_for_connect,
                caller_ip,
                self.port,
            )
            if self._shutting_down or ok is None:
                return web.json_response({"error": "server shutting down"}, status=503)
            if ok:
                clean = self._peer_dest_hash(
                    self.messaging.active_peer_hash or resolved_hash
                )
                self.active_peer = clean
                return web.json_response({"status": "ok", "hash": clean})
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
                clean = self._peer_dest_hash(self.messaging.active_peer_hash or peer_hash)
                self.active_peer = clean
                await self._broadcast({"type": "connect_ok", "data": {"hash": clean}})
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
            if self.messaging and self.messaging.active_link:
                if self._peers_equivalent(resolved, self.messaging.active_peer_hash):
                    return web.json_response({"status": "ok", "connected": True})
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
            if is_android():
                serial_hot = await self._run_blocking(
                    ensure_runtime_serial, settings.get("rns_interfaces")
                )
            msg = "Interface updated."
            if is_android():
                msg = (
                    "Serial interface attached to RNS."
                    if serial_hot
                    else "Settings saved. Select a USB port and grant access if needed."
                )
            else:
                msg = "Interface updated. Restart chatxz to apply."
            return web.json_response({
                "status": "ok",
                "interfaces": self._interfaces_for_api(settings["rns_interfaces"]),
                "serial_hot_added": bool(serial_hot),
                "message": msg,
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    def _beacon_payload(self):
        dest = self._clean_hash(self.destination_hash or "")
        ident = self._clean_hash(self.identity_mgr.get_hex_hash() if self.identity_mgr else "")
        payload = {
            "app": "chatxz",
            "v": 1,
            "hash": dest or ident,
            "name": self.load_settings().get("name", ""),
            "ip": detect_lan_ip() or "",
            "port": self.port,
        }
        if ident and ident != payload["hash"]:
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

    def _reset_network_state(self, update_settings=True):
        if self.messaging:
            self.messaging._teardown_active_link()
        self.active_peer = None
        if self.discovery:
            self.discovery.clear_peers()
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
        if self.discovery:
            self.discovery.clear_peers()
        settings["network_stats_reset_at"] = time.time()
        self.save_settings(settings)
        print("[network] Auto-reset discovery/beacon counters (weekly)")

    async def handle_network_reset(self, request):
        self._reset_network_state(update_settings=True)
        await self._broadcast({"type": "peers", "data": []})
        await self._broadcast({"type": "link_closed", "data": {}})
        await self._broadcast({"type": "network_reset", "data": {}})
        return web.json_response({"status": "ok"})

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
        while True:
            await asyncio.sleep(5)
            if self._shutting_down:
                return
            settings = self.load_settings()
            interfaces = normalize_interface_list(settings.get("rns_interfaces"))
            port, _ = configured_serial_port(interfaces)
            if not port:
                continue
            if serial_port_status(port) == "missing":
                if not serial_detach_sent:
                    self._disable_rns_serial_interfaces()
                    serial_detach_sent = True
            else:
                serial_detach_sent = False
                await self._run_blocking(ensure_runtime_serial, interfaces)

    def _peer_connect_meta(self, peer_hash):
        peer_ip = None
        peer_port = 8742
        stored_ip, stored_port = contact_connect_meta(
            self.config_dir, peer_hash, self._peers_equivalent
        )
        if stored_ip:
            peer_ip = stored_ip
            peer_port = stored_port or peer_port
        for p in (self.discovery.get_peers() if self.discovery else []):
            if not self._peers_equivalent(p.get("hash"), peer_hash):
                continue
            if p.get("ip"):
                peer_ip = p.get("ip")
            peer_port = p.get("port") or peer_port
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
        while True:
            await asyncio.sleep(3)
            if self._shutting_down or not self.messaging:
                continue
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
            await self._run_blocking(ensure_runtime_serial, interfaces)

            peer_ip, peer_port = self._peer_connect_meta(peer)
            print(f"[connect] Failover triggered: {reason}")

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

    async def handle_network_status(self, request):
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
        peers = self.discovery.get_peers() if self.discovery else []
        link_active = bool(self.messaging and self.messaging.active_link)
        active_peer = None
        link_rns_interface = None
        if link_active:
            active_peer = self.active_peer or (
                self.messaging.active_peer_hash if self.messaging else None
            )
            try:
                iface = self.messaging._link_attached_interface(self.messaging.active_link)
                if iface:
                    link_rns_interface = type(iface).__name__
            except Exception:
                pass
        port, _ = configured_serial_port(self.load_settings().get("rns_interfaces"))
        return web.json_response({
            "platform": "android" if is_android() else "desktop",
            "app_version": APP_VERSION,
            "http_bind": f"{self.host}:{self.port}",
            "rns_udp_port": 4242,
            "beacon_udp_port": BEACON_PORT,
            "lan_ip": detect_lan_ip(),
            "broadcast": lan_broadcast(),
            "interfaces": list_network_interfaces(),
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
            "ws_clients": len(self.websockets),
            "link_active": link_active,
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
        })

    async def handle_announce(self, request):
        ok, err = await self._wait_for_rns()
        if not ok:
            return web.json_response({"error": err or "not ready"}, status=400)
        with self._announce_lock:
            try:
                await asyncio.to_thread(self.messaging.announce)
                beacon_sent = 0
                if self.lan_beacon:
                    beacon_sent = await asyncio.to_thread(
                        self.lan_beacon.send, 3, True
                    )
                return web.json_response({
                    "status": "ok",
                    "broadcast": lan_broadcast(),
                    "beacon_port": BEACON_PORT,
                    "beacon_sent": beacon_sent,
                    "lan_ip": detect_lan_ip(),
                })
            except Exception as e:
                return web.json_response({"error": str(e)}, status=400)

    async def handle_disconnect(self, request):
        if self.messaging:
            self.messaging._teardown_active_link(clear_session=True)
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
            if "network_stats_auto_reset" in data:
                settings["network_stats_auto_reset"] = bool(data["network_stats_auto_reset"])
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
            return web.json_response({"status": "restarting", "android": True})
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
        peers = self.discovery.get_peers() if self.discovery else []
        settings = self.load_settings()
        received_dir = settings.get("received_dir", os.path.join(self.config_dir, "received"))
        return web.json_response({
            "identity_hash": self.identity_mgr.get_hex_hash() if self.identity_mgr else None,
            "ws_clients": len(self.websockets),
            "discovered_peers": peers,
            "discovery_running": self.discovery.running if self.discovery else False,
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
            msg_type = media_type_for_filename(fname)

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
                self.messaging.enqueue(msg_type, save_path,
                                        file_name=fname, file_size=size, file_path=save_path)
                return web.json_response({"status": "queued", "name": fname, "size": size})
            my_hash = self._my_sender_hash()
            ts = time.time()
            chat_peer = self._session_chat_peer() or self._peer_dest_hash(self.active_peer)
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

            result = self.messaging.send_file(save_path, msg_type,
                                         progress_callback=self._make_progress_callback(fname, size, transfer_id),
                                         transfer_id=transfer_id)
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
            tmpdir = tempfile.mkdtemp(prefix="chatxz_folder_")
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
            if not self.messaging.active_link:
                self.messaging.enqueue("file", zip_path,
                                        file_name=zip_name, file_size=zsize, file_path=zip_path)
                return web.json_response({"status": "queued", "name": zip_name, "size": zsize})
            my_hash = self._my_sender_hash()
            ts = time.time()
            chat_peer = self._session_chat_peer() or self._peer_dest_hash(self.active_peer)
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
            result = self.messaging.send_file(zip_path, "file",
                                        progress_callback=self._make_progress_callback(zip_name, zsize, transfer_id),
                                        transfer_id=transfer_id)
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

            my_hash = self._my_sender_hash()
            ts = time.time()
            chat_peer = self._session_chat_peer() or self._peer_dest_hash(self.active_peer)
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

            result = self.messaging.send_file(voice_path, "voice",
                                               progress_callback=self._make_progress_callback(voice_name, len(audio_bytes), transfer_id),
                                               transfer_id=transfer_id)
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
            return web.Response(text="Not found: " + full_path, status=404)
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
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.websockets.add(ws)
        print(f"[ws] Client connected ({len(self.websockets)} total)")

        await self._send_peers_to(ws)
        if self.messaging:
            peer = self._peer_dest_hash(
                getattr(self.messaging, "_session_peer_hash", None) or self.active_peer
            )
            if peer and not self.messaging.active_link:
                now = time.time()
                if now - self._session_resume_last >= 8.0:
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
            print(f"[ws] Client disconnected ({len(self.websockets)} total)")
        return ws

    async def _history_maintenance_loop(self):
        while True:
            await asyncio.sleep(60)
            if self._shutting_down:
                return
            self._prune_stale_session_system_messages()

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
                        my_hash = self._my_sender_hash()
                        chat_peer = self._session_chat_peer() or self._peer_dest_hash(self.active_peer)
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
                    self.messaging.enqueue("text", text)
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
                )
                if self._shutting_down or ok is None:
                    await ws.send_str(json.dumps({"type": "connect_fail", "error": "server shutting down"}))
                elif ok:
                    clean = self._peer_dest_hash(
                        self.messaging.active_peer_hash or resolved_hash
                    )
                    self.active_peer = clean
                    await ws.send_str(json.dumps({"type": "connect_ok", "hash": clean}))
                else:
                    await ws.send_str(json.dumps({"type": "connect_fail", "error": "connection failed"}))
        elif msg_type == "announce":
            ok, err = await self._wait_for_rns(timeout=30.0)
            if ok:
                with self._announce_lock:
                    await asyncio.to_thread(self.messaging.announce)
                    if self.lan_beacon:
                        await asyncio.to_thread(self.lan_beacon.send, 3, True)
            elif err:
                await ws.send_str(json.dumps({"type": "info", "data": "Announce failed: " + err}))
        elif msg_type == "read_receipt":
            msg_id = data.get("msg_id", "")
            if msg_id and self.messaging and self.messaging.active_link:
                self.messaging.send_read_receipt(self.messaging.active_link, msg_id)

    async def _on_startup(self, app):
        self._loop = asyncio.get_running_loop()
        self._reset_connection_state()
        self._maybe_auto_reset_network_stats()
        print(f"[startup] Event loop captured: {self._loop}")
        asyncio.create_task(self._discovery_broadcaster())
        self._prune_stale_session_system_messages()
        retention = self.load_settings().get("history_retention", "never")
        if retention == "on_restart":
            self.message_history = []
            self._save_history()
            print("[history] Cleared on restart")
        asyncio.create_task(self._history_maintenance_loop())
        asyncio.create_task(self._link_failover_loop())
        asyncio.create_task(self._serial_watchdog_loop())

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
        app.router.add_get("/api/network-status", self.handle_network_status)
        app.router.add_post("/api/network/reset", self.handle_network_reset)
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
            self._maybe_auto_reset_network_stats()
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

        app = web.Application()
        self._register_routes(app)
        my_hash = self.start_rns()
        app.on_startup.append(self._on_startup)
        app.on_shutdown.append(self._on_shutdown)
        app.on_cleanup.append(self._on_cleanup)

        print(f"chatxz web server v{APP_VERSION}")
        print(f"Your identity: {my_hash}")
        print(f"Web interface: http://{self.host}:{self.port}")
        print("Press Ctrl+C to stop")

        try:
            web.run_app(app, host=self.host, port=self.port, print=lambda _: None)
        except GracefulExit:
            pass


def main():
    import argparse
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
