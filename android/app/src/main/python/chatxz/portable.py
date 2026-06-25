"""Portable desktop launcher — double-click chatxz.exe (Windows) or chatxz.app (macOS)."""

import os
import socket
import sys
import threading
import time
import webbrowser

from chatxz.utils.rns_frozen import ensure_rns_interfaces

if getattr(sys, "frozen", False):
    ensure_rns_interfaces()


def portable_root():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def wait_for_port(host, port, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.25)
    return False


def main():
    root = portable_root()
    os.environ.setdefault("CHATXZ_PORTABLE", root)
    os.chdir(root)
    ensure_rns_interfaces()

    from chatxz._version import __version__ as app_version
    from chatxz.web.server import ChatWebServer

    host = "0.0.0.0"
    port = 8742
    force = sys.platform == "win32" and getattr(sys, "frozen", False)
    server = ChatWebServer(host=host, port=port, verbose=False, debug=False, force=force)

    def _open_browser():
        if wait_for_port("127.0.0.1", port):
            webbrowser.open(f"http://127.0.0.1:{port}")

    threading.Thread(target=_open_browser, name="chatxz-browser", daemon=True).start()

    ip_hint = "ifconfig" if sys.platform == "darwin" else "ipconfig"
    print(f"chatxz v{app_version} (portable)")
    print(f"Web UI:  http://127.0.0.1:{port}")
    print(f"LAN UI:  http://<your-ip>:{port}  (see {ip_hint})")
    print(f"Data:    {os.path.join(root, 'chatxz-data')}")
    print("Press Ctrl+C to stop")

    server.run()


if __name__ == "__main__":
    main()
