"""Tests for LAN/VPN network interface enumeration."""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatxz.utils import platform as plat


class LinuxInterfaceHelpers(unittest.TestCase):
    def test_skip_container_not_vpn(self):
        self.assertTrue(plat._linux_skip_iface("veth123"))
        self.assertTrue(plat._linux_skip_iface("docker0"))
        self.assertFalse(plat._linux_skip_iface("wg0"))
        self.assertFalse(plat._linux_skip_iface("tun0"))
        self.assertFalse(plat._linux_skip_iface("tailscale0"))
        self.assertFalse(plat._linux_skip_iface("enp2s0"))

    def test_tunnel_detection_by_name(self):
        for name in ("wg0", "tun0", "tap0", "ppp0", "tailscale0", "nordlynx", "zt0"):
            self.assertTrue(plat._linux_is_tunnel_iface(name), name)
        self.assertFalse(plat._linux_is_tunnel_iface("enp2s0"))
        self.assertFalse(plat._linux_is_tunnel_iface("wlo1"))

    def test_auto_priority_prefers_ethernet_over_vpn(self):
        self.assertGreater(
            plat._linux_iface_auto_priority("enp2s0", "10.0.30.112"),
            plat._linux_iface_auto_priority("wg0", "10.0.30.112"),
        )

    def test_enumerate_includes_tunnel_when_present(self):
        with patch.object(plat, "is_android", return_value=False):
            with patch("os.listdir", return_value=["lo", "enp2s0", "wg0", "veth0"]):
                with patch.object(plat, "_linux_iface_entry") as mock_entry:
                    mock_entry.side_effect = lambda n: {
                        "name": n,
                        "kind": "vpn" if n == "wg0" else "ethernet",
                        "ip": "10.0.0.1" if n != "lo" else "disconnected",
                        "broadcast": None,
                        "subnet_broadcast": None,
                        "up": n != "lo",
                    }
                    names = [e["name"] for e in plat.enumerate_lan_interfaces()]
        self.assertIn("enp2s0", names)
        self.assertIn("wg0", names)
        self.assertNotIn("lo", names)
        self.assertNotIn("veth0", names)

    def test_pinned_vpn_ip_resolution(self):
        plat.set_lan_interface_preference("wg0")
        try:
            with patch.object(plat, "is_android", return_value=False):
                with patch.object(plat, "_linux_skip_iface", return_value=False):
                    with patch.object(plat, "_linux_is_tunnel_iface", return_value=True):
                        with patch.object(plat, "_linux_iface_ipv4", return_value="100.64.0.2"):
                            with patch.object(plat, "_linux_iface_link_up", return_value=False):
                                self.assertEqual(plat.lan_ip(), "100.64.0.2")
        finally:
            plat.set_lan_interface_preference(None)


    def test_physical_lan_skips_vpn(self):
        with patch.object(plat, "is_android", return_value=False):
            with patch.object(plat, "_linux_enumerate_interfaces") as mock_enum:
                mock_enum.return_value = [
                    {"name": "wg0", "kind": "vpn", "ip": "10.10.100.12", "up": True},
                    {"name": "enp2s0", "kind": "ethernet", "ip": "disconnected", "up": False},
                ]
                self.assertFalse(plat.physical_lan_reachable())
                mock_enum.return_value[1]["ip"] = "10.0.30.112"
                mock_enum.return_value[1]["up"] = True
                self.assertTrue(plat.physical_lan_reachable())


class WindowsInterfaceHelpers(unittest.TestCase):
    def test_windows_enumerate_parses_powershell_json(self):
        payload = (
            '[{"name":"Ethernet 2","ip":"10.0.47.37","up":true,"gateway_iface":true},'
            '{"name":"Tailscale","ip":"100.64.0.2","up":true,"gateway_iface":false}]'
        )
        with patch.object(plat.subprocess, "run") as mock_run:
            mock_run.return_value.stdout = payload
            mock_run.return_value.returncode = 0
            entries = plat._windows_enumerate_interfaces()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["ip"], "10.0.47.37")
        self.assertEqual(entries[0]["broadcast"], "10.0.47.255")
        self.assertEqual(entries[1]["kind"], "vpn")

    def test_desktop_lan_ip_prefers_gateway_interface(self):
        entries = [
            {"name": "Tailscale", "kind": "vpn", "ip": "100.64.0.2", "up": True},
            {
                "name": "Ethernet 2",
                "kind": "ethernet",
                "ip": "10.0.47.37",
                "up": True,
                "gateway_iface": True,
            },
            {
                "name": "Ethernet 2",
                "kind": "ethernet",
                "ip": "192.168.1.37",
                "up": True,
                "gateway_iface": False,
            },
        ]
        with patch.object(plat, "get_lan_interface_preference", return_value=None):
            with patch.object(plat, "_desktop_enumerate_interfaces", return_value=entries):
                self.assertEqual(plat._desktop_lan_ip(), "10.0.47.37")

    def test_physical_lan_true_on_windows_entries(self):
        entries = [
            {
                "name": "Ethernet 2",
                "kind": "ethernet",
                "ip": "10.0.47.37",
                "up": True,
                "gateway_iface": True,
            },
        ]
        with patch.object(plat, "is_android", return_value=False):
            with patch.object(plat.sys, "platform", "win32"):
                with patch.object(plat, "_desktop_enumerate_interfaces", return_value=entries):
                    self.assertTrue(plat.physical_lan_reachable())
                    self.assertTrue(plat.lan_connected())


if __name__ == "__main__":
    unittest.main()