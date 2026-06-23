"""Capture process stdout/stderr to a text file (Android Downloads) for field debugging."""

import atexit
import os
import sys
import threading
from datetime import datetime

_lock = threading.Lock()
_log_path = None
_orig_stdout = None
_orig_stderr = None


class _TeeStream:
    def __init__(self, original, log_path):
        self._original = original
        self._path = log_path
        self._file = open(log_path, "a", encoding="utf-8", errors="replace")
        self._file.write(
            f"\n--- chatxz debug log {datetime.now().isoformat()} ---\n"
        )
        self._file.flush()

    def write(self, data):
        if not data:
            return 0
        try:
            self._original.write(data)
        except Exception:
            pass
        with _lock:
            try:
                self._file.write(data)
                self._file.flush()
            except Exception:
                pass
        return len(data)

    def flush(self):
        try:
            self._original.flush()
        except Exception:
            pass
        with _lock:
            try:
                self._file.flush()
            except Exception:
                pass

    def isatty(self):
        return False

    def fileno(self):
        try:
            return self._original.fileno()
        except Exception:
            raise OSError("no fileno")

    def close_log(self):
        with _lock:
            try:
                self._file.close()
            except Exception:
                pass


def _dir_writable(path):
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".chatxz_write_test")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def android_app_downloads_dir():
    try:
        from java import jclass
        python = jclass("com.chaquo.python.Python")
        ctx = python.getPlatform().getApplication()
        environment = jclass("android.os.Environment")
        files = ctx.getExternalFilesDir(environment.DIRECTORY_DOWNLOADS)
        if files is not None:
            path = str(files.getAbsolutePath())
            if path:
                return path
    except Exception:
        pass
    return None


def android_public_downloads_dir():
    try:
        from java import jclass
        environment = jclass("android.os.Environment")
        downloads = environment.getExternalStoragePublicDirectory(
            environment.DIRECTORY_DOWNLOADS
        )
        if downloads is not None:
            path = str(downloads.getAbsolutePath())
            if path:
                return path
    except Exception:
        pass
    return None


def resolve_android_debug_dir():
    """Prefer app-private paths — public Downloads often needs extra storage permission."""
    try:
        from chatxz.utils.platform import android_files_dir
        files = android_files_dir()
        if files:
            logs = os.path.join(files, "debug_logs")
            if _dir_writable(logs):
                return logs, "app debug_logs folder"
    except Exception:
        pass
    app_dl = android_app_downloads_dir()
    if app_dl and _dir_writable(app_dl):
        return app_dl, "app Downloads folder"
    public = android_public_downloads_dir()
    if public and _dir_writable(public):
        return public, "phone Downloads"
    return None, ""


def debug_log_path():
    return _log_path


def debug_log_tail(max_bytes=32000):
    """Return the tail of the active debug log for in-app viewing."""
    path = _log_path
    if not path or not os.path.isfile(path):
        return None
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
            data = fh.read()
        return data.decode("utf-8", errors="replace")
    except OSError:
        return None


def start_debug_capture():
    """Mirror stdout/stderr to Downloads/chatxz-debug-*.txt on Android."""
    global _log_path, _orig_stdout, _orig_stderr
    if _log_path:
        return _log_path
    try:
        from chatxz.utils.platform import is_android
        if not is_android():
            return None
    except Exception:
        return None

    downloads, label = resolve_android_debug_dir()
    if not downloads:
        return None

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(downloads, f"chatxz-debug-{stamp}.txt")
    latest = os.path.join(downloads, "chatxz-debug-latest.txt")

    _orig_stdout = sys.stdout
    _orig_stderr = sys.stderr
    sys.stdout = _TeeStream(_orig_stdout, path)
    sys.stderr = _TeeStream(_orig_stderr, path)
    _log_path = path

    try:
        with open(latest, "w", encoding="utf-8") as fh:
            fh.write(path + "\n")
    except OSError:
        pass

    print(f"[debug-log] Capturing logs to {path} ({label})")
    atexit.register(stop_debug_capture)
    return path


def stop_debug_capture():
    global _log_path, _orig_stdout, _orig_stderr
    for attr, orig in (("stdout", _orig_stdout), ("stderr", _orig_stderr)):
        stream = getattr(sys, attr, None)
        if stream is not None and hasattr(stream, "close_log"):
            try:
                stream.close_log()
            except Exception:
                pass
        if orig is not None:
            setattr(sys, attr, orig)
    _orig_stdout = None
    _orig_stderr = None
    _log_path = None
