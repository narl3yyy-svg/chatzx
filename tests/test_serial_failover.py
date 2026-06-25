"""Tests for dual-path serial failover helpers."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatxz.core import rns_interfaces as ri
from chatxz.core.messaging import MessagingBackend


class SerialConfigTests(unittest.TestCase):
    def test_configured_serial_enabled_ignores_stale_enabled_flag(self):
        interfaces = [
            {
                "id": "s1",
                "preset": "serial",
                "type": "SerialInterface",
                "port": "/dev/ttyUSB0",
                "enabled": False,
            }
        ]
        with patch.object(ri, "serial_runtime_active", return_value=True):
            self.assertTrue(ri.configured_serial_enabled(interfaces))

    def test_render_includes_serial_when_port_accessible(self):
        interfaces = [
            {
                "id": "s1",
                "preset": "serial",
                "type": "SerialInterface",
                "port": "/dev/ttyUSB0",
                "speed": 57600,
            }
        ]
        with patch.object(ri, "serial_runtime_active", return_value=True):
            text = ri.render_rns_config(interfaces, broadcast_ip="10.0.30.255", log=None)
        self.assertIn("type = SerialInterface", text)
        self.assertIn("port = /dev/ttyUSB0", text)
        self.assertNotIn("hot-add at runtime", text)

    def test_render_skips_serial_when_port_missing(self):
        interfaces = [
            {
                "id": "s1",
                "preset": "serial",
                "type": "SerialInterface",
                "port": "/dev/ttyUSB0",
            }
        ]
        with patch.object(ri, "serial_runtime_active", return_value=False):
            with patch.object(ri, "serial_skip_reason", return_value=("/dev/ttyUSB0", "not connected")):
                text = ri.render_rns_config(interfaces, broadcast_ip="10.0.30.255", log=None)
        self.assertNotIn("type = SerialInterface", text)


class _FakeIdentity:
    def __init__(self, ident_hex):
        self.hash = bytes.fromhex(ident_hex)


class _FakeLink:
    ACTIVE = 2

    def __init__(self, link_id_hex, rtt=None, iface=None):
        self.link_id = bytes.fromhex(link_id_hex.ljust(32, "0")[:32])
        self.status = self.ACTIVE
        self.rtt = rtt
        self.attached_interface = iface


class FailoverPreferenceTests(unittest.TestCase):
    def _backend(self):
        ident = _FakeIdentity("a" * 32)
        backend = MessagingBackend(
            identity=ident,
            config_dir="/tmp/chatxz-test",
        )
        backend.running = True
        backend.my_dest_hash = "b" * 32
        return backend

    def test_failover_families_prefer_lan_by_default(self):
        backend = self._backend()
        peer = "4a2aa1dbbed382886b0333274e546ba8"
        with patch.object(backend, "_has_online_family", side_effect=lambda fam: fam in ("udp", "serial")):
            with patch.object(backend, "_serial_transport_ready", return_value=True):
                with patch("chatxz.core.messaging.physical_lan_reachable", return_value=True):
                    with patch.object(backend, "_serial_faster_than_lan", return_value=False):
                        families = backend._failover_families_to_try(peer)
        self.assertEqual(families[0], "udp")
        self.assertIn("serial", families)

    def test_failover_families_prefer_serial_when_faster(self):
        backend = self._backend()
        peer = "4a2aa1dbbed382886b0333274e546ba8"
        with patch.object(backend, "_has_online_family", side_effect=lambda fam: fam in ("udp", "serial")):
            with patch.object(backend, "_serial_transport_ready", return_value=True):
                with patch("chatxz.core.messaging.physical_lan_reachable", return_value=True):
                    with patch.object(backend, "_serial_faster_than_lan", return_value=True):
                        families = backend._failover_families_to_try(peer)
        self.assertEqual(families[0], "serial")

    def test_serial_faster_than_lan_uses_rtt(self):
        backend = self._backend()
        peer = "4a2aa1dbbed382886b0333274e546ba8"
        serial_iface = MagicMock()
        udp_iface = MagicMock()
        serial_link = _FakeLink("11" * 16, rtt=0.01, iface=serial_iface)
        udp_link = _FakeLink("22" * 16, rtt=0.05, iface=udp_iface)
        backend.links[serial_link.link_id] = serial_link
        backend.links[udp_link.link_id] = udp_link
        backend.active_link = serial_link
        backend.peer_links[peer] = serial_link
        backend._link_peer_hashes[serial_link.link_id] = peer
        backend._link_peer_hashes[udp_link.link_id] = peer
        with patch.object(backend, "_serial_transport_ready", return_value=True):
            with patch("chatxz.core.messaging.physical_lan_reachable", return_value=True):
                with patch.object(backend, "_has_online_family", return_value=True):
                    with patch.object(backend, "_peer_has_path_on_family", return_value=True):
                        with patch("chatxz.core.messaging.interface_family", side_effect=lambda i: (
                            "serial" if i is serial_iface else "udp"
                        )):
                            self.assertTrue(backend._serial_faster_than_lan(peer))

    def test_on_serial_transport_attached_announces(self):
        backend = self._backend()
        backend.destination = MagicMock()
        with patch.object(backend, "_burst_serial_announce", return_value=3) as burst:
            backend.on_serial_transport_attached(MagicMock(port="/dev/ttyUSB0"))
        burst.assert_called_once()


if __name__ == "__main__":
    unittest.main()