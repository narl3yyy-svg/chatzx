"""Tests for TCP hub relay: remote group chat over port 4242, isolated from LAN/UDP."""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatxz.core import rns_interfaces as ri
from chatxz.core.lan_rns import interface_family
from chatxz.core.messaging import (
    ChatMessage,
    HUB_GROUP_PEER,
    MESSAGE_TYPE_TEXT,
    MessagingBackend,
    is_hub_peer_hash,
)


class HubServerBindingTests(unittest.TestCase):
    def test_hub_server_listens_on_all_interfaces(self):
        from chatxz.web.server import ChatWebServer

        server = ChatWebServer.__new__(ChatWebServer)
        settings = {
            "hub_role": "server",
            "hub_port": 4242,
            "rns_interfaces": ri.default_interface_list(),
        }
        out = server._apply_hub_settings(settings)
        tcp_srv = next(
            i for i in out["rns_interfaces"]
            if i.get("type") == "TCPServerInterface"
        )
        self.assertEqual(tcp_srv.get("listen_ip"), "0.0.0.0")
        self.assertEqual(tcp_srv.get("listen_port"), 4242)
        self.assertTrue(tcp_srv.get("enabled"))

    def test_hub_server_custom_port(self):
        from chatxz.web.server import ChatWebServer

        server = ChatWebServer.__new__(ChatWebServer)
        settings = {
            "hub_role": "server",
            "hub_port": 54242,
            "rns_interfaces": ri.default_interface_list(),
        }
        out = server._apply_hub_settings(settings)
        tcp_srv = next(
            i for i in out["rns_interfaces"]
            if i.get("type") == "TCPServerInterface"
        )
        self.assertEqual(tcp_srv.get("listen_port"), 54242)


class HubClientRemoteTests(unittest.TestCase):
    def test_hub_client_targets_public_hostname(self):
        from chatxz.web.server import ChatWebServer

        server = ChatWebServer.__new__(ChatWebServer)
        settings = {
            "hub_role": "client",
            "hub_host": "hub.example.com",
            "hub_port": 4242,
            "rns_interfaces": ri.default_interface_list(),
        }
        out = server._apply_hub_settings(settings)
        client = next(
            i for i in out["rns_interfaces"]
            if i.get("type") == "TCPClientInterface"
        )
        self.assertEqual(client.get("target_host"), "hub.example.com")
        self.assertEqual(client.get("target_port"), 4242)
        self.assertTrue(client.get("enabled"))

    def test_hub_client_without_host_leaves_interfaces_unchanged(self):
        from chatxz.web.server import ChatWebServer

        server = ChatWebServer.__new__(ChatWebServer)
        before = ri.default_interface_list()
        settings = {
            "hub_role": "client",
            "hub_host": "",
            "hub_port": 4242,
            "rns_interfaces": before,
        }
        out = server._apply_hub_settings(settings)
        self.assertEqual(
            [i.get("preset") for i in out["rns_interfaces"]],
            [i.get("preset") for i in before],
        )

    def test_hub_client_disables_tcp_server_listener(self):
        from chatxz.web.server import ChatWebServer

        server = ChatWebServer.__new__(ChatWebServer)
        settings = {
            "hub_role": "client",
            "hub_host": "203.0.113.50",
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
        self.assertTrue(servers)
        self.assertTrue(all(not s.get("enabled") for s in servers))


class HubMessageFormatTests(unittest.TestCase):
    def test_chat_message_hub_flag_serializes(self):
        msg = ChatMessage(MESSAGE_TYPE_TEXT, "hello remote hub")
        msg.hub_group = True
        payload = json.loads(msg.to_json())
        self.assertTrue(payload.get("hub"))
        restored = ChatMessage.from_json(msg.to_json())
        self.assertTrue(restored.hub_group)

    def test_regular_message_has_no_hub_flag(self):
        msg = ChatMessage(MESSAGE_TYPE_TEXT, "p2p only")
        payload = json.loads(msg.to_json())
        self.assertNotIn("hub", payload)


class HubRelayIsolationTests(unittest.TestCase):
    def _backend(self, hub_role="server"):
        ident = MagicMock()
        ident.hash = bytes.fromhex("a" * 32)
        tmp = tempfile.mkdtemp()
        settings_path = os.path.join(tmp, "settings.json")
        with open(settings_path, "w", encoding="utf-8") as fh:
            json.dump({"hub_role": hub_role}, fh)
        backend = MessagingBackend(identity=ident, config_dir=tmp)

        class UDPInterface:
            pass

        class TCPClientInterface:
            pass

        udp_link = MagicMock()
        udp_link.link_id = "udp1"
        udp_link.mtu = 500
        udp_link.attached_interface = UDPInterface()
        tcp_a = MagicMock()
        tcp_a.link_id = "tcp1"
        tcp_a.mtu = 500
        tcp_a.attached_interface = TCPClientInterface()
        tcp_b = MagicMock()
        tcp_b.link_id = "tcp2"
        tcp_b.mtu = 500
        tcp_b.attached_interface = TCPClientInterface()

        backend.peer_links = {
            "b" * 32: udp_link,
            "c" * 32: tcp_a,
            "d" * 32: tcp_b,
        }
        backend.links = {
            "udp1": udp_link,
            "tcp1": tcp_a,
            "tcp2": tcp_b,
        }
        return backend, udp_link, tcp_a, tcp_b

    def test_hub_tcp_peers_excludes_udp_p2p(self):
        backend, _, tcp_a, tcp_b = self._backend()
        peers = backend._hub_tcp_linked_peers()
        self.assertEqual(set(peers), {"c" * 32, "d" * 32})

    def test_relay_reaches_all_tcp_clients_not_udp(self):
        backend, _, tcp_a, tcp_b = self._backend()
        msg = MagicMock()
        msg.hub_group = True
        msg.to_json.return_value = '{"hub":true,"type":"text"}'
        with patch("chatxz.core.messaging.RNS.Packet") as pkt:
            backend.relay_hub_message(msg, sender_hash="c" * 32)
            targets = {call.args[0] for call in pkt.call_args_list}
            self.assertEqual(targets, {tcp_b})

    def test_send_hub_message_never_targets_udp_link(self):
        backend, udp_link, tcp_a, tcp_b = self._backend(hub_role="server")
        with patch("chatxz.core.messaging.RNS.Packet") as pkt:
            backend.send_hub_message(
                "remote group",
                hub_server_mode=True,
            )
            self.assertEqual(pkt.call_count, 2)
            sent_links = {call.args[0] for call in pkt.call_args_list}
            self.assertEqual(sent_links, {tcp_a, tcp_b})
            self.assertNotIn(udp_link, sent_links)

    def test_relay_ignores_non_hub_messages(self):
        backend, _, tcp_a, _ = self._backend()
        msg = MagicMock()
        msg.hub_group = False
        with patch("chatxz.core.messaging.RNS.Packet") as pkt:
            backend.relay_hub_message(msg, sender_hash="c" * 32)
            pkt.assert_not_called()


class HubDefaultsAndSettingsTests(unittest.TestCase):
    def test_default_hub_role_is_off(self):
        from chatxz.web.server import ChatWebServer

        server = ChatWebServer.__new__(ChatWebServer)
        server.config_dir = tempfile.mkdtemp()
        with patch.object(
            ChatWebServer,
            "load_settings",
            wraps=server.load_settings,
        ):
            with patch("builtins.open", side_effect=FileNotFoundError):
                defaults = server.load_settings()
        self.assertEqual(defaults.get("hub_role"), "off")
        self.assertEqual(defaults.get("hub_port"), 4242)
        self.assertEqual(defaults.get("hub_host"), "")

    def test_hub_group_peer_is_not_a_real_dest_hash(self):
        self.assertTrue(is_hub_peer_hash(HUB_GROUP_PEER))
        self.assertFalse(is_hub_peer_hash("a" * 32))


class HubTransportFamilyTests(unittest.TestCase):
    def test_tcp_interface_family_for_hub_links(self):
        class TCPClientInterface:
            pass

        class TCPServerInterface:
            pass

        class UDPInterface:
            pass

        self.assertEqual(interface_family(TCPClientInterface()), "tcp")
        self.assertEqual(interface_family(TCPServerInterface()), "tcp")
        self.assertEqual(interface_family(UDPInterface()), "udp")

    def test_hub_transport_active_when_role_set(self):
        ident = MagicMock()
        ident.hash = bytes.fromhex("a" * 32)
        tmp = tempfile.mkdtemp()
        settings_path = os.path.join(tmp, "settings.json")
        with open(settings_path, "w", encoding="utf-8") as fh:
            json.dump({"hub_role": "client", "hub_host": "1.2.3.4"}, fh)
        backend = MessagingBackend(identity=ident, config_dir=tmp)
        self.assertTrue(backend._hub_transport_active())
        with open(settings_path, "w", encoding="utf-8") as fh:
            json.dump({"hub_role": "off"}, fh)
        self.assertFalse(backend._hub_transport_active())


class HubHeadlessSpecTests(unittest.TestCase):
    """Specification tests for planned dedicated headless hub mode (not yet implemented)."""

    def test_headless_hub_setting_not_in_defaults_yet(self):
        from chatxz.web.server import ChatWebServer

        server = ChatWebServer.__new__(ChatWebServer)
        server.config_dir = tempfile.mkdtemp()
        with patch("builtins.open", side_effect=FileNotFoundError):
            defaults = server.load_settings()
        self.assertNotIn("headless_hub", defaults)

    def test_server_mode_supports_tcp_only_relay_path(self):
        """Headless hub will reuse hub_role=server + TCP listener on 4242."""
        from chatxz.web.server import ChatWebServer

        server = ChatWebServer.__new__(ChatWebServer)
        settings = {
            "hub_role": "server",
            "hub_port": 4242,
            "rns_interfaces": [],
        }
        out = server._apply_hub_settings(settings)
        tcp_srv = next(
            i for i in out["rns_interfaces"]
            if i.get("type") == "TCPServerInterface"
        )
        self.assertTrue(tcp_srv.get("enabled"))
        self.assertEqual(tcp_srv.get("listen_ip"), "0.0.0.0")


if __name__ == "__main__":
    unittest.main()