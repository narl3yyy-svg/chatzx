"""Portable Windows/desktop launcher — double-click chatxz.exe to start."""

import importlib
import os
import socket
import sys
import threading
import time
import webbrowser


def _preload_rns():
    """Ensure RNS interface modules load before Reticulum (PyInstaller/frozen builds)."""
    if not getattr(sys, "frozen", False):
        return
    for mod in (
        "RNS.Interfaces.Interface",
        "RNS.Interfaces.UDPInterface",
        "RNS.Interfaces.AutoInterface",
        "RNS.Interfaces.TCPInterface",
        "RNS.Interfaces.LocalInterface",
    ):
        try:
            importlib.import_module(mod)
        except Exception:
            pass
    try:
        import RNS.Interfaces as rns_ifaces
        names = (
            "Interface", "UDPInterface", "AutoInterface", "TCPInterface",
            "LocalInterface", "SerialInterface", "BackboneInterface",
        )
        if not getattr(rns_ifaces, "__all__", None) or "Interface" not in rns_ifaces.__all__:
            rns_ifaces.__all__ = list(names)
    except Exception:
        pass


def portable_root():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def wait_for_port(host, port, timeout=90.0):
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
    _preload_rns()

    from chatxz._version import __version__ as app_version
    from chatxz.web.server import ChatWebServer

    host = "0.0.0.0"
    port = 8742
    server = ChatWebServer(host=host, port=port, verbose=False, debug=False, force=False)

    thread = threading.Thread(target=server.run, name="chatxz-server", daemon=True)
    thread.start()

    if wait_for_port("127.0.0.1", port):
        webbrowser.open(f"http://127.0.0.1:{port}")

    print(f"chatxz v{app_version} (portable)")
    print(f"Web UI:  http://127.0.0.1:{port}")
    print(f"LAN UI:  http://<your-ip>:{port}  (see ipconfig)")
    print(f"Data:    {os.path.join(root, 'chatxz-data')}")
    print("Press Ctrl+C to stop")

    try:
        while thread.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping chatxz...")


if __name__ == "__main__":
    main()