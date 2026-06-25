"""Tests for Linux UDP defaults and hub peer isolation."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatxz.core import rns_interfaces as ri
from chatxz.core.discovery import PeerDiscovery
from chatxz.core.messaging import is_hub_peer_hash, HUB_GROUP_PEER


class DefaultInterfaceTests(unittest.TestCase):
    def test_desktop_default_is_udp_lan(self):
        from unittest.mock import patch
        with patch("chatxz.utils.platform.is_android", return_value=False):
            items = ri.default_interface_list()
        self.assertTrue(items)
        self.assertEqual(items[0].get("type"), "UDPInterface")
        self.assertEqual(items[0].get("preset"), "udp_lan")

    def test_standalone_needs_udp_for_loopback_tcp_only(self):
        ifaces = ri.normalize_interface_list([
            {
                "id": "tcp1",
                "preset": "tcp_client",
                "name": "TCP Client",
                "enabled": True,
                "target_host": "127.0.0.1",
                "target_port": 4242,
            }
        ])
        self.assertTrue(ri.standalone_needs_udp(ifaces))


class HubSettingsTests(unittest.TestCase):
    def test_apply_hub_server_enables_tcp_listener(self):
        from chatxz.web.server import ChatWebServer

        server = ChatWebServer.__new__(ChatWebServer)
        settings = {
            "hub_role": "server",
            "hub_port": 4242,
            "rns_interfaces": ri.default_interface_list(),
        }
        out = server._apply_hub_settings(settings)
        server_iface = next(
            i for i in out["rns_interfaces"]
            if i.get("type") == "TCPServerInterface"
        )
        self.assertTrue(server_iface.get("enabled"))
        self.assertEqual(server_iface.get("listen_ip"), "0.0.0.0")
        self.assertEqual(server_iface.get("listen_port"), 4242)

    def test_apply_hub_client_points_tcp_client_at_host(self):
        from chatxz.web.server import ChatWebServer

        server = ChatWebServer.__new__(ChatWebServer)
        settings = {
            "hub_role": "client",
            "hub_host": "10.10.100.11",
            "hub_port": 4242,
            "rns_interfaces": ri.default_interface_list(),
        }
        out = server._apply_hub_settings(settings)
        client = next(
            i for i in out["rns_interfaces"]
            if i.get("type") == "TCPClientInterface"
        )
        self.assertEqual(client.get("target_host"), "10.10.100.11")
        self.assertEqual(client.get("target_port"), 4242)
        self.assertTrue(client.get("enabled"))


class HubPeerTests(unittest.TestCase):
    def test_is_hub_peer_hash(self):
        self.assertTrue(is_hub_peer_hash(HUB_GROUP_PEER))
        self.assertTrue(is_hub_peer_hash("__hub_group__"))
        self.assertFalse(is_hub_peer_hash("deadbeefdeadbeefdeadbeefdeadbeef"))

    def test_discovery_subnet_scope(self):
        disc = PeerDiscovery()
        disc.accept_peers = True
        now = __import__("time").time()
        disc.peers["a"] = {
            "hash": "a" * 32,
            "ip": "10.0.30.101",
            "last_seen": now,
            "via": "beacon",
        }
        disc.peers["b"] = {
            "hash": "b" * 32,
            "ip": "10.10.100.4",
            "last_seen": now,
            "via": "beacon",
        }
        scoped = disc.get_peers(scope_ip="10.0.30.112")
        ips = {p.get("ip") for p in scoped}
        self.assertIn("10.0.30.101", ips)
        self.assertNotIn("10.10.100.4", ips)


if __name__ == "__main__":
    unittest.main()