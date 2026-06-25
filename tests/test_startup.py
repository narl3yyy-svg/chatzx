"""Smoke tests for deferred RNS startup and interface toggles."""

import os
import signal
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatxz.core import rns_interfaces as ri
from chatxz.utils import platform as plat


class StartupTests(unittest.TestCase):
    def test_signal_patch_allows_worker_thread_handlers(self):
        plat.patch_embedded_signals()
        errors = []

        def worker():
            try:
                signal.signal(signal.SIGINT, signal.SIG_DFL)
            except ValueError as exc:
                errors.append(str(exc))

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        self.assertEqual(errors, [])

    def test_disabled_udp_omitted_from_config(self):
        ifaces = ri.normalize_interface_list([
            {
                "id": "udp1",
                "preset": "udp_lan",
                "name": "UDP LAN",
                "enabled": False,
            }
        ])
        text = ri.render_rns_config(
            ifaces, broadcast_ip="10.0.30.255", auto_interface_enabled=True,
        )
        self.assertNotIn("type = UDPInterface", text)
        self.assertNotIn("type = AutoInterface", text)

    def test_auto_interface_toggle(self):
        ifaces = ri.normalize_interface_list([
            {"id": "udp1", "preset": "udp_lan", "name": "UDP LAN", "enabled": True},
        ])
        with_auto = ri.render_rns_config(
            ifaces, broadcast_ip="10.0.30.255", auto_interface_enabled=True,
        )
        without_auto = ri.render_rns_config(
            ifaces, broadcast_ip="10.0.30.255", auto_interface_enabled=False,
        )
        if sys.platform == "win32":
            self.skipTest("AutoInterface not rendered on Windows")
        # Explicit UDP LAN already binds :4242 — AutoInterface must not duplicate it.
        self.assertNotIn("type = AutoInterface", with_auto)
        self.assertNotIn("type = AutoInterface", without_auto)
        serial_only = ri.render_rns_config(
            ri.normalize_interface_list([
                {"id": "s1", "preset": "serial", "name": "Serial", "port": "/dev/ttyUSB0", "enabled": True},
            ]),
            auto_interface_enabled=True,
        )
        if sys.platform != "win32":
            self.assertIn("type = AutoInterface", serial_only)

    def test_serial_user_disabled_stays_off(self):
        items = ri.normalize_interface_list([
            {
                "id": "s1",
                "preset": "serial",
                "name": "Serial",
                "port": "/dev/ttyUSB0",
                "enabled": True,
            }
        ])
        updated = ri.update_interface(items, "s1", {"enabled": False})
        serial = next(i for i in updated if i["id"] == "s1")
        self.assertFalse(serial["enabled"])
        self.assertTrue(serial.get("user_disabled"))


if __name__ == "__main__":
    unittest.main()