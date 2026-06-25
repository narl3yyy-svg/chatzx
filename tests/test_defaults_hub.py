"""Tests for Linux UDP defaults and hub peer isolation."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatxz.core import rns_interfaces as ri
from chatxz.core.discovery import PeerDiscovery
from chatxz.core.lan_rns import interface_family
from chatxz.core.messaging import is_hub_peer_hash, HUB_GROUP_PEER, MessagingBackend


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

    def test_apply_hub_server_disables_all_tcp_clients(self):
        from chatxz.web.server import ChatWebServer

        server = ChatWebServer.__new__(ChatWebServer)
        settings = {
            "hub_role": "server",
            "hub_port": 4242,
            "rns_interfaces": [
                {
                    "id": "c1",
                    "preset": "tcp_client",
                    "type": "TCPClientInterface",
                    "enabled": True,
                    "target_host": "10.10.100.11",
                    "target_port": 4242,
                },
                {
                    "id": "c2",
                    "preset": "tcp_client",
                    "type": "TCPClientInterface",
                    "enabled": True,
                    "target_host": "127.0.0.1",
                    "target_port": 4242,
                },
            ],
        }
        out = server._apply_hub_settings(settings)
        clients = [
            i for i in out["rns_interfaces"]
            if i.get("type") == "TCPClientInterface"
        ]
        self.assertTrue(clients)
        self.assertTrue(all(not c.get("enabled") for c in clients))

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


class TcpFamilyTests(unittest.TestCase):
    def test_interface_family_recognizes_tcp(self):
        class TCPClientInterface:
            pass
        class TCPServerInterface:
            pass
        self.assertEqual(interface_family(TCPClientInterface()), "tcp")
        self.assertEqual(interface_family(TCPServerInterface()), "tcp")


class HubFailoverTests(unittest.TestCase):
    def test_failover_uses_tcp_in_hub_mode(self):
        from unittest.mock import MagicMock, patch

        ident = MagicMock()
        ident.hash = bytes.fromhex("a" * 32)
        backend = MessagingBackend(identity=ident, config_dir="/tmp/chatxz-hub-test")
        backend.running = True
        with patch.object(backend, "_hub_transport_active", return_value=True):
            with patch.object(backend, "_has_online_family", return_value=False):
                families = backend._failover_families_to_try("b" * 32)
        self.assertEqual(families, ["tcp"])


class HubPeerTests(unittest.TestCase):
    def test_is_hub_peer_hash(self):
        self.assertTrue(is_hub_peer_hash(HUB_GROUP_PEER))
        self.assertTrue(is_hub_peer_hash("__hub_group__"))
        self.assertFalse(is_hub_peer_hash("deadbeefdeadbeefdeadbeefdeadbeef"))

    def test_discovery_auto_mode_shows_all_subnets(self):
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
        scoped_none = disc.get_peers(scope_ip=None)
        ips = {p.get("ip") for p in scoped_none}
        self.assertIn("10.0.30.101", ips)
        self.assertIn("10.10.100.4", ips)

    def test_discovery_scope_ip_unpinned_is_none(self):
        from chatxz.web.server import ChatWebServer

        server = ChatWebServer.__new__(ChatWebServer)
        server.config_dir = "/tmp/chatxz-test"
        from unittest.mock import patch
        with patch.object(
            ChatWebServer, "load_settings", return_value={
                "hub_role": "off",
                "lan_interface": "",
                "rns_interfaces": ri.default_interface_list(),
            }
        ):
            self.assertIsNone(server._discovery_scope_ip())

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