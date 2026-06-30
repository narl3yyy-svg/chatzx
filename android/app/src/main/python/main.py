"""Android entry point — starts the Rust chatxz application."""

import os
import socket
import subprocess
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

PUBLIC_PORT = 8742
WEB_HOST = "127.0.0.1"
_chatxz_proc = None
_server_started = False
_server_error = []


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
    if str(flag).strip().lower() in ("1", "true", "yes", "on"):
        os.environ["CHATXZ_DEBUG"] = "1"
    else:
        os.environ.pop("CHATXZ_DEBUG", None)


def _find_chatxz_binary():
    root = os.environ.get("CHATXZ_ROOT", "")
    candidates = [
        os.path.join(root, "target", "release", "chatxz"),
        os.path.join(root, "android", "app", "src", "main", "assets", "bin", "chatxz"),
    ]
    files_dir = os.environ.get("CHATXZ_FILES_DIR")
    if files_dir:
        candidates.insert(0, os.path.join(files_dir, "chatxz"))
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


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
    global _server_started, _chatxz_proc
    _startup_log("start_server() called")
    if _server_started and _wait_for_port(WEB_HOST, PUBLIC_PORT, timeout=3):
        return WEB_HOST, str(PUBLIC_PORT)

    try:
        from chatxz.utils.platform import android_files_dir, is_android
        files_dir = android_files_dir()
        if files_dir:
            os.environ["CHATXZ_FILES_DIR"] = files_dir
        if not is_android():
            os.environ["CHATXZ_ANDROID"] = "1"
    except Exception as e:
        return "None", f"Platform init: {type(e).__name__}: {e}"

    bin_path = _find_chatxz_binary()
    if not bin_path:
        return "None", "chatxz Rust binary not found — rebuild APK with scripts/build-rust-android.sh"

    def _run():
        global _chatxz_proc
        try:
            _startup_log("starting Rust chatxz")
            env = os.environ.copy()
            cmd = [bin_path, "--port", str(PUBLIC_PORT)]
            if os.environ.get("CHATXZ_DEBUG") == "1":
                cmd.append("--verbose")
            _chatxz_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
            )
            _startup_log(f"chatxz pid={_chatxz_proc.pid}")
        except Exception:
            err = traceback.format_exc()
            _server_error.append(err)
            _startup_log(f"start error:\n{err}")

    _server_started = True
    threading.Thread(target=_run, name="chatxz-app", daemon=True).start()

    timeout = 120 if os.environ.get("CHATXZ_DEBUG") == "1" else 45
    if not _wait_for_port(WEB_HOST, PUBLIC_PORT, timeout=timeout):
        _server_started = False
        if _server_error:
            return "None", _server_error[0][-4000:]
        return "None", f"Server timeout — port {PUBLIC_PORT} did not open in {timeout}s"

    _startup_log("chatxz ready")
    return WEB_HOST, str(PUBLIC_PORT)