"""Transport-isolated multi-peer routing — prevents serial/LAN cross-traffic."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatxz.core.messaging import MessagingBackend


UBUNTU = "f1c2ac9061239f7c096701f02969729c"
WINDOWS = "87a012c46dc2274afccae6fe597b8675"
ARCH = "436ce5fd79d0932dc10b24da54a180f8"


class _FakeIdentity:
    def __init__(self, ident_hex):
        self.hash = bytes.fromhex(ident_hex)


class _FakeLink:
    ACTIVE = 2

    def __init__(self, link_id_hex, iface=None, remote_dest=None):
        self.link_id = bytes.fromhex(link_id_hex.ljust(32, "0")[:32])
        self.status = self.ACTIVE
        self.rtt = 0.01
        self.attached_interface = iface
        self._remote_dest = remote_dest

    def get_remote_identity(self):
        if not self._remote_dest:
            return None
        ident = _FakeIdentity(self._remote_dest)
        return ident


def _iface(family):
    m = MagicMock()
    m.family = family
    return m


class CrossTrafficRoutingTests(unittest.TestCase):
    def _backend(self, resolver=None):
        ident = _FakeIdentity("a" * 32)
        backend = MessagingBackend(
            identity=ident,
            config_dir="/tmp/chatxz-cross-traffic",
        )
        backend.running = True
        backend.my_dest_hash = "b" * 32
        backend.peer_transport_resolver = resolver
        return backend

    def _wire_dual_links(self, backend):
        serial_iface = _iface("serial")
        udp_iface = _iface("udp")
        ubuntu_serial = _FakeLink("11" * 16, serial_iface, UBUNTU)
        windows_udp = _FakeLink("22" * 16, udp_iface, WINDOWS)
        backend.links[ubuntu_serial.link_id] = ubuntu_serial
        backend.links[windows_udp.link_id] = windows_udp
        backend._link_peer_hashes[ubuntu_serial.link_id] = UBUNTU
        backend._link_peer_hashes[windows_udp.link_id] = WINDOWS
        backend.peer_links[UBUNTU] = ubuntu_serial
        backend.peer_links[WINDOWS] = windows_udp
        backend.active_link = windows_udp
        backend.active_peer_hash = WINDOWS
        backend._session_peer_hash = WINDOWS
        return ubuntu_serial, windows_udp

    def test_send_to_serial_peer_never_uses_lan_link(self):
        resolver = lambda h: {
            "hash": h,
            "via": "serial" if h == UBUNTU else "rns",
            "ip": "10.10.10.2" if h == WINDOWS else "",
        }
        backend = self._backend(resolver)
        ubuntu_serial, windows_udp = self._wire_dual_links(backend)

        with patch("chatxz.core.messaging.interface_family", side_effect=lambda i: (
            "serial" if i is ubuntu_serial.attached_interface else "udp"
        )):
            with patch.object(backend, "_link_interface_healthy", return_value=True):
                with patch.object(
                    backend,
                    "_dest_hash_from_identity",
                    side_effect=lambda ident: UBUNTU if ident.hash.hex().startswith("f1c2") else WINDOWS,
                ):
                    link = backend._queue_send_link(UBUNTU)
        self.assertIs(link, ubuntu_serial)
        self.assertIsNot(link, windows_udp)

    def test_send_to_lan_peer_never_uses_serial_link(self):
        resolver = lambda h: {
            "hash": h,
            "via": "serial" if h == UBUNTU else "rns",
            "ip": "10.10.10.2" if h == WINDOWS else "",
        }
        backend = self._backend(resolver)
        ubuntu_serial, windows_udp = self._wire_dual_links(backend)

        with patch("chatxz.core.messaging.interface_family", side_effect=lambda i: (
            "serial" if i is ubuntu_serial.attached_interface else "udp"
        )):
            with patch.object(backend, "_peer_lan_ip_usable", return_value=True):
                with patch.object(backend, "_link_interface_healthy", return_value=True):
                    with patch.object(
                        backend,
                        "_dest_hash_from_identity",
                        side_effect=lambda ident: UBUNTU if ident.hash.hex().startswith("f1c2") else WINDOWS,
                    ):
                        link = backend._queue_send_link(WINDOWS)
        self.assertIs(link, windows_udp)

    def test_link_for_peer_no_active_link_fallback(self):
        backend = self._backend()
        udp_iface = _iface("udp")
        windows_udp = _FakeLink("22" * 16, udp_iface, WINDOWS)
        backend.links[windows_udp.link_id] = windows_udp
        backend.peer_links[WINDOWS] = windows_udp
        backend._link_peer_hashes[windows_udp.link_id] = WINDOWS
        backend.active_peer_hash = WINDOWS
        backend.active_link = windows_udp
        self.assertIsNone(backend._link_for_peer(UBUNTU))

    def test_parallel_sessions_skip_teardown_other_peers(self):
        backend = self._backend()
        with patch.object(backend, "_parallel_sessions_allowed", return_value=True):
            closed = backend._teardown_other_peer_links(UBUNTU)
        self.assertEqual(closed, 0)

    def test_notify_link_established_parallel_does_not_steal_session(self):
        backend = self._backend()
        ubuntu_serial, windows_udp = self._wire_dual_links(backend)

        with patch.object(backend, "_parallel_sessions_allowed", return_value=True):
            with patch.object(backend, "_consolidate_peer_links", return_value=0):
                backend._notify_link_established(
                    ubuntu_serial, UBUNTU, promote_active=True,
                )
        self.assertIs(backend.active_link, windows_udp)
        self.assertEqual(backend.active_peer_hash, WINDOWS)
        self.assertEqual(backend.peer_links.get(UBUNTU), ubuntu_serial)

    def test_link_needs_failover_blocks_cross_transport_in_parallel_mode(self):
        resolver = lambda h: {
            "hash": h,
            "via": "serial",
            "name": "UBUNTU",
        }
        backend = self._backend(resolver)
        ubuntu_serial, _ = self._wire_dual_links(backend)
        backend.active_link = ubuntu_serial
        backend.active_peer_hash = UBUNTU
        backend._session_peer_hash = UBUNTU
        backend._last_link_established_at = 0

        with patch.object(backend, "_parallel_sessions_allowed", return_value=True):
            with patch.object(backend, "_link_interface_healthy", return_value=True):
                with patch.object(backend, "_has_online_family", return_value=True):
                    with patch("chatxz.core.messaging.physical_lan_reachable", return_value=True):
                        with patch.object(backend, "_peer_has_path_on_family", return_value=True):
                            with patch("chatxz.core.messaging.interface_family", return_value="serial"):
                                needs, reason = backend.link_needs_failover()
        self.assertFalse(needs)

    def test_link_needs_failover_lan_peer_stays_on_lan_in_parallel_mode(self):
        resolver = lambda h: {
            "hash": h,
            "via": "rns",
            "ip": "10.10.10.2",
        }
        backend = self._backend(resolver)
        _, windows_udp = self._wire_dual_links(backend)

        with patch.object(backend, "_parallel_sessions_allowed", return_value=True):
            with patch.object(backend, "_link_interface_healthy", return_value=True):
                with patch.object(backend, "_peer_lan_ip_usable", return_value=True):
                    with patch("chatxz.core.messaging.interface_family", return_value="udp"):
                        needs, _ = backend.link_needs_failover()
        self.assertFalse(needs)

    def test_adopt_link_does_not_promote_non_session_peer(self):
        backend = self._backend()
        ubuntu_serial, windows_udp = self._wire_dual_links(backend)

        with patch.object(backend, "_link_interface_healthy", return_value=True):
            with patch("chatxz.core.messaging.interface_family", return_value="serial"):
                with patch.object(backend, "_notify_link_established") as notify:
                    adopted = backend._adopt_healthy_peer_link(UBUNTU)
        self.assertIs(adopted, ubuntu_serial)
        notify.assert_not_called()

    def test_peer_send_ready_requires_transport_safe_link(self):
        resolver = lambda h: {"hash": h, "via": "serial" if h == UBUNTU else "rns", "ip": ""}
        backend = self._backend(resolver)
        self._wire_dual_links(backend)

        with patch("chatxz.core.messaging.interface_family", side_effect=lambda i: (
            "serial" if getattr(i, "family", "") == "serial" else "udp"
        )):
            with patch.object(backend, "_link_interface_healthy", return_value=True):
                with patch.object(
                    backend,
                    "_dest_hash_from_identity",
                    side_effect=lambda ident: UBUNTU if ident.hash.hex().startswith("f1c2") else WINDOWS,
                ):
                    self.assertTrue(backend.peer_send_ready(UBUNTU))
                    self.assertTrue(backend.peer_send_ready(WINDOWS))

    def test_send_blocked_when_link_remote_identity_differs(self):
        resolver = lambda h: {"hash": h, "via": "serial" if h == UBUNTU else "rns", "ip": ""}
        backend = self._backend(resolver)
        ubuntu_serial, windows_udp = self._wire_dual_links(backend)
        backend.peer_links[UBUNTU] = windows_udp

        with patch("chatxz.core.messaging.interface_family", side_effect=lambda i: (
            "serial" if i is ubuntu_serial.attached_interface else "udp"
        )):
            with patch.object(backend, "_link_interface_healthy", return_value=True):
                with patch.object(
                    backend,
                    "_dest_hash_from_identity",
                    side_effect=lambda ident: UBUNTU if ident.hash.hex().startswith("f1c2") else WINDOWS,
                ):
                    result = backend.send_message("hello", target_peer=UBUNTU)
        self.assertFalse(result)

    def test_register_peer_link_rejects_wrong_remote(self):
        backend = self._backend()
        udp_iface = _iface("udp")
        windows_udp = _FakeLink("22" * 16, udp_iface, WINDOWS)
        backend.links[windows_udp.link_id] = windows_udp
        with patch.object(
            backend,
            "_dest_hash_from_identity",
            side_effect=lambda ident: WINDOWS,
        ):
            backend._register_peer_link(windows_udp, UBUNTU)
        self.assertNotIn(UBUNTU, backend.peer_links)

    def test_beacon_does_not_pollute_serial_peer_with_lan_ip(self):
        from chatxz.core.discovery import PeerDiscovery

        disc = PeerDiscovery()
        disc.peers[UBUNTU] = {
            "hash": UBUNTU,
            "name": "UBUNTU",
            "via": "serial",
            "last_seen": 1,
        }
        with patch.object(disc, "_peer_allowed", return_value=True):
            with patch.object(disc, "_sanitize_peer_scope", side_effect=lambda p: p):
                with patch(
                    "chatxz.core.discovery.serial_discovery_active",
                    return_value=True,
                ):
                    with patch(
                        "chatxz.core.discovery.register_identity_from_beacon",
                        return_value=True,
                    ):
                        disc._on_beacon(
                            {
                                "app": "chatxz",
                                "hash": UBUNTU,
                                "name": "UBUNTU",
                                "ip": "10.10.10.10",
                                "port": 8742,
                                "identity_hash": UBUNTU,
                            },
                            "b" * 32,
                            my_identity_hash="a" * 32,
                            source_ip="10.10.10.10",
                        )
        peer = disc.peers.get(UBUNTU) or {}
        self.assertEqual(peer.get("via"), "serial")
        self.assertNotIn("ip", peer)

    def test_best_outgoing_prefers_serial_for_serial_zone_peer(self):
        resolver = lambda h: {"hash": h, "via": "serial"}
        backend = self._backend(resolver)
        serial_iface = _iface("serial")
        udp_iface = _iface("udp")
        serial_link = _FakeLink("11" * 16, serial_iface, UBUNTU)
        udp_link = _FakeLink("22" * 16, udp_iface, UBUNTU)
        backend.links[serial_link.link_id] = serial_link
        backend.links[udp_link.link_id] = udp_link
        backend._link_peer_hashes[serial_link.link_id] = UBUNTU
        backend._link_peer_hashes[udp_link.link_id] = UBUNTU
        backend.peer_links[UBUNTU] = serial_link

        with patch("chatxz.core.messaging.interface_family", side_effect=lambda i: (
            "serial" if i is serial_iface else "udp"
        )):
            with patch.object(backend, "_link_interface_healthy", return_value=True):
                with patch.object(
                    backend,
                    "_dest_hash_from_identity",
                    return_value=UBUNTU,
                ):
                    chosen = backend._best_outgoing_link(UBUNTU)
        self.assertIs(chosen, serial_link)


class DiscoveryResolverTests(unittest.TestCase):
    def test_dual_transport_prefers_serial_without_explicit_ip(self):
        from chatxz.web.server import ChatWebServer

        server = ChatWebServer.__new__(ChatWebServer)
        server.discovery = MagicMock()
        serial_peer = {
            "hash": ARCH,
            "name": "ARCH",
            "via": "serial",
        }
        rns_peer = {
            "hash": ARCH,
            "name": "ARCH",
            "via": "rns",
            "ip": "10.10.10.37",
        }
        server._scoped_peers = lambda: [serial_peer, rns_peer]
        server._discovery_scope_ip = lambda: "10.0.5.10"

        with patch(
            "chatxz.core.transport_isolation.dual_transport_isolation_enabled",
            return_value=True,
        ):
            meta = server._discovery_peer_for_connect(None, ARCH)
        self.assertEqual(meta.get("via"), "serial")

    def test_explicit_lan_ip_uses_rns_entry(self):
        from chatxz.web.server import ChatWebServer

        server = ChatWebServer.__new__(ChatWebServer)
        server.discovery = MagicMock()
        serial_peer = {"hash": ARCH, "name": "ARCH", "via": "serial"}
        rns_peer = {
            "hash": ARCH,
            "name": "ARCH",
            "via": "rns",
            "ip": "10.10.10.37",
        }
        server._scoped_peers = lambda: [serial_peer, rns_peer]
        server._discovery_scope_ip = lambda: "10.10.10.37"

        with patch(
            "chatxz.core.transport_isolation.dual_transport_isolation_enabled",
            return_value=True,
        ):
            meta = server._discovery_peer_for_connect("10.10.10.37", ARCH)
        self.assertEqual(meta.get("via"), "rns")
        self.assertEqual(meta.get("ip"), "10.10.10.37")


class DiscoveryDedupTests(unittest.TestCase):
    def test_serial_evicts_stale_rns_duplicate(self):
        from chatxz.core.discovery import PeerDiscovery

        disc = PeerDiscovery()
        stale_hash = "aabbccddaabbccddaabbccddaabbccdd"
        disc.peers[stale_hash] = {
            "hash": stale_hash,
            "name": "ARCH",
            "via": "rns",
            "ip": "10.10.10.37",
            "last_seen": 1,
        }
        with patch.object(disc, "_peer_allowed", return_value=True):
            with patch.object(disc, "_sanitize_peer_scope", side_effect=lambda p: p):
                with patch(
                    "chatxz.core.discovery.serial_discovery_active",
                    return_value=True,
                ):
                    disc._store_peer({
                        "hash": ARCH,
                        "name": "ARCH",
                        "via": "serial",
                        "last_seen": 2,
                    })
        self.assertNotIn(stale_hash, disc.peers)
        self.assertIn(ARCH, disc.peers)


if __name__ == "__main__":
    unittest.main()