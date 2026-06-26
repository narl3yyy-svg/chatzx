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

    def test_set_primary_lan_transport_swaps_udp_for_tcp(self):
        udp = ri.default_interface_list()
        self.assertTrue(any(i.get("preset") == "udp_lan" for i in udp))
        tcp = ri.set_primary_lan_transport(udp, "tcp_lan")
        self.assertFalse(any(i.get("preset") == "udp_lan" for i in tcp))
        self.assertTrue(any(i.get("preset") == "tcp_lan" for i in tcp))


class LanTransportHubPolicyTests(unittest.TestCase):
    def test_tcp_lan_blocked_for_hub_server(self):
        policy = ri.lan_transport_hub_policy("server", "tcp_lan")
        self.assertFalse(policy["allowed"])
        self.assertIn("4242", policy["warning"])

    def test_tcp_lan_allowed_with_warning_for_hub_client(self):
        policy = ri.lan_transport_hub_policy("client", "tcp_lan")
        self.assertTrue(policy["allowed"])
        self.assertIn("local", policy["warning"].lower())

    def test_tcp_lan_allowed_when_hub_off(self):
        policy = ri.lan_transport_hub_policy("off", "tcp_lan")
        self.assertTrue(policy["allowed"])
        self.assertEqual(policy["warning"], "")

    def test_udp_lan_always_allowed(self):
        for role in ("off", "server", "client"):
            policy = ri.lan_transport_hub_policy(role, "udp_lan")
            self.assertTrue(policy["allowed"])


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

    def test_apply_hub_client_keeps_tcp_lan_enabled(self):
        from chatxz.web.server import ChatWebServer

        server = ChatWebServer.__new__(ChatWebServer)
        settings = {
            "hub_role": "client",
            "hub_host": "10.0.30.109",
            "hub_port": 4242,
            "rns_interfaces": [{
                "id": "tcp-lan",
                "preset": "tcp_lan",
                "type": "TCPServerInterface",
                "enabled": True,
                "listen_ip": "0.0.0.0",
                "listen_port": 4242,
            }],
        }
        out = server._apply_hub_settings(settings)
        tcp_lan = next(
            i for i in out["rns_interfaces"]
            if i.get("preset") == "tcp_lan"
        )
        self.assertTrue(tcp_lan.get("enabled"))

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


class TcpLanTests(unittest.TestCase):
    def test_tcp_lan_enables_lan_discovery(self):
        ifaces = ri.normalize_interface_list([{
            "id": "tcp-lan",
            "preset": "tcp_lan",
            "enabled": True,
        }])
        self.assertTrue(ri.configured_tcp_lan_enabled(ifaces))
        self.assertTrue(ri.lan_discovery_configured(ifaces))
        self.assertFalse(ri.configured_udp_lan_enabled(ifaces))

    def test_standalone_accepts_tcp_lan_without_udp(self):
        ifaces = ri.normalize_interface_list([{
            "id": "tcp-lan",
            "preset": "tcp_lan",
            "enabled": True,
        }])
        self.assertFalse(ri.standalone_needs_udp(ifaces))

    def test_render_tcp_lan_config(self):
        ifaces = ri.normalize_interface_list([{
            "id": "tcp-lan",
            "preset": "tcp_lan",
            "name": "TCP LAN",
            "enabled": True,
            "listen_ip": "0.0.0.0",
            "listen_port": 4242,
        }])
        cfg = ri.render_rns_config(ifaces, android=False)
        self.assertIn("TCPServerInterface", cfg)
        self.assertIn("listen_port = 4242", cfg)
        self.assertNotIn("UDPInterface", cfg)
        self.assertNotIn("AutoInterface", cfg)

    def test_apply_hub_server_reuses_tcp_lan_listener(self):
        from chatxz.web.server import ChatWebServer

        server = ChatWebServer.__new__(ChatWebServer)
        settings = {
            "hub_role": "server",
            "hub_port": 4242,
            "rns_interfaces": [{
                "id": "tcp-lan",
                "preset": "tcp_lan",
                "type": "TCPServerInterface",
                "enabled": True,
                "listen_ip": "0.0.0.0",
                "listen_port": 4242,
            }],
        }
        out = server._apply_hub_settings(settings)
        servers = [
            i for i in out["rns_interfaces"]
            if i.get("type") == "TCPServerInterface"
        ]
        self.assertEqual(len(servers), 1)
        self.assertTrue(servers[0].get("enabled"))


class TcpFamilyTests(unittest.TestCase):
    def test_finalize_rns_interface_sets_ifac_netname(self):
        class _Iface:
            DEFAULT_IFAC_SIZE = 16

            def optimise_mtu(self):
                pass

        iface = _Iface()
        ri._finalize_rns_interface(iface, ifac_size=16)
        self.assertTrue(hasattr(iface, "ifac_netname"))
        self.assertIsNone(iface.ifac_netname)
        self.assertTrue(hasattr(iface, "ifac_netkey"))
        self.assertIsNone(iface.ifac_netkey)

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


class HubGroupIsolationTests(unittest.TestCase):
    def _backend_with_links(self, hub_role="off", hub_host="10.0.30.109"):
        from unittest.mock import MagicMock, patch
        import json
        import os
        import tempfile

        ident = MagicMock()
        ident.hash = bytes.fromhex("a" * 32)
        tmp = tempfile.mkdtemp()
        settings_path = os.path.join(tmp, "settings.json")
        with open(settings_path, "w", encoding="utf-8") as fh:
            json.dump({
                "hub_role": hub_role,
                "hub_host": hub_host,
                "hub_port": 4242,
            }, fh)
        backend = MessagingBackend(identity=ident, config_dir=tmp)

        class UDPInterface:
            pass

        class TCPClientInterface:
            target_host = hub_host
            target_port = 4242

        class TCPServerInterface:
            pass

        udp_link = MagicMock()
        udp_link.link_id = "udp1"
        udp_link.attached_interface = UDPInterface()
        tcp_link = MagicMock()
        tcp_link.link_id = "tcp1"
        if hub_role == "server":
            tcp_link.attached_interface = TCPServerInterface()
        else:
            tcp_link.attached_interface = TCPClientInterface()

        backend.peer_links = {
            "b" * 32: udp_link,
            "c" * 32: tcp_link,
        }
        backend.links = {"udp1": udp_link, "tcp1": tcp_link}
        return backend, udp_link, tcp_link

    def test_hub_send_targets_server_uses_tcp_only(self):
        backend, _, _ = self._backend_with_links(hub_role="server")
        targets = backend._hub_send_targets(hub_server_mode=True)
        self.assertEqual(targets, ["c" * 32])

    def test_hub_send_targets_client_uses_tcp_only(self):
        backend, _, _ = self._backend_with_links(hub_role="client")
        targets = backend._hub_send_targets(hub_server_hash="c" * 32)
        self.assertEqual(targets, ["c" * 32])
        self.assertEqual(backend._hub_send_targets(hub_server_hash="b" * 32), [])

    def test_relay_hub_message_skips_udp_peers(self):
        from unittest.mock import MagicMock, patch

        backend, udp_link, tcp_link = self._backend_with_links(hub_role="server")
        msg = MagicMock()
        msg.hub_group = True
        msg.to_json.return_value = '{"hub":true}'
        with patch("chatxz.core.messaging.RNS.Packet") as pkt:
            backend.relay_hub_message(msg, sender_hash="d" * 32)
            pkt.assert_called_once_with(tcp_link, b'{"hub":true}')

    def test_hub_message_rejected_when_hub_off(self):
        from unittest.mock import MagicMock

        backend, udp_link, _ = self._backend_with_links(hub_role="off")
        msg = MagicMock()
        msg.hub_group = True
        self.assertFalse(backend._hub_message_acceptable(msg, udp_link))

    def test_hub_message_rejected_on_udp_when_hub_client(self):
        from unittest.mock import MagicMock

        backend, udp_link, tcp_link = self._backend_with_links(hub_role="client")
        msg = MagicMock()
        msg.hub_group = True
        self.assertFalse(backend._hub_message_acceptable(msg, udp_link))
        self.assertTrue(backend._hub_message_acceptable(msg, tcp_link))

    def test_tcp_lan_p2p_link_not_hub_relay_target(self):
        from unittest.mock import MagicMock

        backend, _, _ = self._backend_with_links(hub_role="server")

        class TCPClientInterface:
            target_host = "10.0.30.101"
            target_port = 4242

        lan_tcp = MagicMock()
        lan_tcp.link_id = "lan1"
        lan_tcp.attached_interface = TCPClientInterface()
        backend.peer_links["e" * 32] = lan_tcp
        backend.links["lan1"] = lan_tcp
        self.assertNotIn("e" * 32, backend._hub_tcp_linked_peers())

    def test_on_message_drops_hub_group_when_hub_off(self):
        from chatxz.web.server import ChatWebServer
        from unittest.mock import MagicMock, patch

        server = ChatWebServer.__new__(ChatWebServer)
        server.config_dir = "/tmp/chatxz-hub-off"
        server.message_history = []
        server.websockets = []
        server._loop = None
        server.debug = False
        chat_msg = MagicMock()
        chat_msg.hub_group = True
        with patch.object(
            ChatWebServer, "load_settings", return_value={"hub_role": "off"}
        ), patch.object(ChatWebServer, "_save_history") as save_hist:
            server._on_message(chat_msg, "b" * 32)
        save_hist.assert_not_called()


class HubPeerTests(unittest.TestCase):
    def test_is_hub_peer_hash(self):
        self.assertTrue(is_hub_peer_hash(HUB_GROUP_PEER))
        self.assertTrue(is_hub_peer_hash("__hub_group__"))
        self.assertFalse(is_hub_peer_hash("deadbeefdeadbeefdeadbeefdeadbeef"))

    def test_discovery_unscoped_shows_all_subnets(self):
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

    def test_discovery_scope_ip_auto_uses_primary_lan(self):
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
        ), patch(
            "chatxz.web.server.detect_lan_ip", return_value="10.10.100.12",
        ):
            self.assertEqual(server._discovery_scope_ip(), "10.10.100.12")

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
        scoped = disc.get_peers(scope_ip="10.10.100.12")
        ips = {p.get("ip") for p in scoped}
        self.assertIn("10.10.100.4", ips)
        self.assertNotIn("10.0.30.101", ips)


if __name__ == "__main__":
    unittest.main()