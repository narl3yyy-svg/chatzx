"""Android entry point — starts the full chatxz web server in a background thread."""

import os
import socket
import threading
import time
import traceback

os.environ.setdefault("CHATXZ_ANDROID", "1")
os.environ.setdefault("ANDROID_ROOT", "/system")
os.environ.setdefault("ANDROID_ARGUMENT", "")

try:
    from chatxz.android_usb.bootstrap import bootstrap as bootstrap_android_usb
    bootstrap_android_usb()
except Exception:
    pass

try:
    from chatxz.utils.platform import patch_embedded_signals
    patch_embedded_signals()
except Exception:
    pass

# Bind on all interfaces so LAN peers can reach beacon-ingest and file transfer.
BIND_HOST, PORT = "0.0.0.0", 8742
# WebView always loads the local loopback URL.
WEB_HOST = "127.0.0.1"
_server_error = []
_server_started = False


def _startup_log_path():
    base = os.environ.get("CHATXZ_FILES_DIR") or "."
    return os.path.join(base, "chatxz-startup.log")


def _startup_log(msg):
    try:
        with open(_startup_log_path(), "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def set_debug_mode(flag="0"):
    """Called from MainActivity before start_server (string avoids Chaquopy bool issues)."""
    if str(flag).strip().lower() in ("1", "true", "yes", "on"):
        os.environ["CHATXZ_DEBUG"] = "1"
    else:
        os.environ.pop("CHATXZ_DEBUG", None)


def _wait_for_port(host, port, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _server_error:
            return False
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            s.connect((host, port))
            return True
        except OSError:
            time.sleep(0.25)
        finally:
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
    return False


def start_server():
    """Called from MainActivity via Chaquopy. Returns (host, port) or (None, error)."""
    global _server_started
    _startup_log("start_server() called")
    if _server_started and _wait_for_port(WEB_HOST, PORT, timeout=3):
        _startup_log("reusing existing server")
        return WEB_HOST, str(PORT)
    try:
        from chatxz.utils.platform import android_files_dir, is_android
        files_dir = android_files_dir()
        if files_dir:
            os.environ["CHATXZ_FILES_DIR"] = files_dir
        if not is_android():
            os.environ["CHATXZ_ANDROID"] = "1"
    except Exception as e:
        _startup_log(f"platform init failed: {e}")
        return "None", f"Platform init: {type(e).__name__}: {e}"

    debug_mode = os.environ.get("CHATXZ_DEBUG") == "1"
    _startup_log(f"debug_mode={debug_mode}")

    if debug_mode:
        try:
            from chatxz.utils.debug_log import start_debug_capture
            path = start_debug_capture()
            if path:
                _startup_log(f"debug capture enabled: {path}")
        except Exception as exc:
            _startup_log(f"debug capture failed: {exc}")

    def _run():
        try:
            _startup_log("server thread starting")
            from chatxz.web.server import ChatWebServer
            _startup_log("ChatWebServer import ok")
            server = ChatWebServer(
                host=BIND_HOST,
                port=PORT,
                verbose=debug_mode,
                debug=debug_mode,
                force=False,
                embedded=True,
            )
            _startup_log("run_embedded()")
            server.run_embedded()
        except Exception:
            err = traceback.format_exc()
            _server_error.append(err)
            _startup_log(f"server thread error:\n{err}")

    _server_started = True
    thread = threading.Thread(target=_run, name="chatxz-server", daemon=True)
    thread.start()

    port_timeout = 120 if debug_mode else 45
    _startup_log(f"waiting for port 8742 (timeout={port_timeout}s)")
    if not _wait_for_port(WEB_HOST, PORT, timeout=port_timeout):
        _server_started = False
        if _server_error:
            err = _server_error[0]
            _startup_log(f"failed: {err[:500]}")
            if len(err) > 4000:
                err = err[-4000:]
            return "None", err
        _startup_log("failed: port timeout")
        return "None", f"Server timeout — port 8742 did not open in {port_timeout}s"

    _startup_log("server ready")
    return WEB_HOST, str(PORT)