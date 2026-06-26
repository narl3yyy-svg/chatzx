"""Tests for dual-path serial failover helpers."""

import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatxz.core import rns_interfaces as ri
from chatxz.core.messaging import MessagingBackend


class _FakeSerial:
    def __init__(self, open_=True):
        self.is_open = open_


class _FakeSerialIface:
    def __init__(self, port, online=False, ready=False):
        self.port = port
        self.online = online
        if ready:
            self.mode = 1
            self.serial = _FakeSerial(True)
        else:
            self.serial = None


class SerialConnectPreferenceTests(unittest.TestCase):
    def _backend(self):
        ident = _FakeIdentity("a" * 32)
        backend = MessagingBackend(
            identity=ident,
            config_dir="/tmp/chatxz-test",
        )
        backend.running = True
        return backend

    def test_should_prefer_serial_for_ipless_discovery_peer(self):
        backend = self._backend()
        peer_hash = "b" * 32
        lookup = lambda _ip, _h: {"hash": peer_hash, "name": "UBUNTU", "via": "serial"}
        with patch.object(backend, "_serial_transport_ready", return_value=True):
            self.assertTrue(
                backend._should_prefer_serial_connect(peer_hash, peer_ip=None, peer_lookup=lookup)
            )

    def test_should_not_prefer_serial_when_in_scope_lan_ip(self):
        backend = self._backend()
        peer_hash = "c" * 32
        with patch.object(backend, "_serial_transport_ready", return_value=True):
            with patch.object(backend, "_peer_lan_ip_usable", return_value=True):
                self.assertFalse(
                    backend._should_prefer_serial_connect(
                        peer_hash, peer_ip="10.10.10.2", peer_lookup=None,
                    )
                )

    def test_should_prefer_serial_when_meta_serial_despite_stale_contact_ip(self):
        backend = self._backend()
        peer_hash = "f1c2ac9061239f7c096701f02969729c"
        lookup = lambda _ip, _h: {
            "hash": peer_hash,
            "name": "UBUNTU",
            "via": "serial",
        }
        with patch.object(backend, "_serial_transport_ready", return_value=True):
            with patch.object(backend, "_peer_lan_ip_usable", return_value=True):
                self.assertTrue(
                    backend._should_prefer_serial_connect(
                        peer_hash,
                        peer_ip="10.10.10.10",
                        peer_lookup=lookup,
                    )
                )

    def test_burst_serial_announce_force_ignores_connect_guard(self):
        backend = self._backend()
        backend._connect_in_progress = True
        backend.destination = MagicMock()
        with patch.object(backend, "_serial_transport_ready", return_value=True):
            with patch.object(backend, "_announce_on_interface", return_value=True) as announce:
                with patch("chatxz.core.messaging.serial_interface_online", return_value=MagicMock(port="/dev/ttyUSB0")):
                    with patch("chatxz.core.messaging.suppress_offline_lan_transports"):
                        with patch("chatxz.core.messaging.dedupe_serial_interfaces"):
                            with patch("chatxz.core.messaging.prune_dead_serial_interfaces"):
                                sent = backend._burst_serial_announce(count=1, force=True)
        self.assertEqual(sent, 1)
        announce.assert_called()

    def test_udp_connect_ready_false_for_stale_non_udp_path(self):
        backend = self._backend()
        peer_hash = "d" * 32
        with patch("chatxz.core.messaging.physical_lan_reachable", return_value=True):
            with patch.object(backend, "_lan_transport_ready", return_value=True):
                with patch("chatxz.core.messaging.configured_udp_lan_enabled", return_value=True):
                    with patch.object(backend, "_peer_has_path_on_family", return_value=False):
                        with patch.object(backend, "_peer_has_path", return_value=True):
                            self.assertFalse(
                                backend._udp_connect_ready(
                                    peer_hash, peer_ip=None, prefer_serial=False,
                                )
                            )

    def test_udp_connect_ready_false_when_serial_preferred(self):
        backend = self._backend()
        with patch("chatxz.core.messaging.physical_lan_reachable", return_value=True):
            with patch.object(backend, "_lan_transport_ready", return_value=True):
                with patch("chatxz.core.messaging.configured_udp_lan_enabled", return_value=True):
                    self.assertFalse(
                        backend._udp_connect_ready("e" * 32, peer_ip="10.10.10.2", prefer_serial=True)
                    )


class SerialRuntimeEnsureTests(unittest.TestCase):
    def test_ensure_runtime_serial_waits_for_existing_interface(self):
        iface = _FakeSerialIface("/dev/ttyUSB0", online=False)
        with patch.object(ri, "configured_serial_port", return_value=("/dev/ttyUSB0", 57600)):
            with patch.object(ri, "serial_port_accessible", return_value=True):
                with patch.object(ri, "find_serial_interface", return_value=iface):
                    with patch.object(ri, "serial_interface_is_ready", side_effect=[False, True]):
                        with patch.object(ri, "hot_add_serial_interface") as hot_add:
                            with patch.object(ri, "time") as mock_time:
                                mock_time.sleep = lambda _: None
                                mock_time.time = time.time
                                result = ri.ensure_runtime_serial([])
        hot_add.assert_not_called()
        self.assertIs(result, iface)

    def test_ensure_runtime_serial_returns_initializing_interface_without_hot_add(self):
        iface = _FakeSerialIface("/dev/ttyUSB0", online=False)
        with patch.object(ri, "configured_serial_port", return_value=("/dev/ttyUSB0", 57600)):
            with patch.object(ri, "serial_port_accessible", return_value=True):
                with patch.object(ri, "find_serial_interface", return_value=iface):
                    with patch.object(ri, "serial_interface_is_ready", return_value=False):
                        with patch.object(ri, "hot_add_serial_interface") as hot_add:
                            with patch.object(ri, "time") as mock_time:
                                mock_time.sleep = lambda _: None
                                mock_time.time = time.time
                                result = ri.ensure_runtime_serial([])
        hot_add.assert_not_called()
        self.assertIs(result, iface)

    def test_hot_add_serial_skips_when_interface_already_present(self):
        iface = _FakeSerialIface("/dev/ttyUSB0", online=True, ready=True)
        with patch.object(ri, "serial_port_accessible", return_value=True):
            with patch.object(ri, "dedupe_serial_interfaces"):
                with patch.object(ri, "find_serial_interface", return_value=iface):
                    with patch("RNS.Interfaces.SerialInterface.SerialInterface") as ctor:
                        result = ri.hot_add_serial_interface("/dev/ttyUSB0")
        ctor.assert_not_called()
        self.assertIs(result, iface)


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
        udp_only = [{
            "id": "udp1",
            "preset": "udp_lan",
            "type": "UDPInterface",
            "enabled": True,
        }]
        with patch.object(backend, "_hub_transport_active", return_value=False):
            with patch(
                "chatxz.core.messaging.load_settings_interfaces",
                return_value=udp_only,
            ):
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

    def test_failover_skips_lan_for_hub_server_peer(self):
        backend = self._backend()
        with patch.object(backend, "_hub_transport_active", return_value=True):
            with patch.object(backend, "_peer_uses_hub_transport", return_value=True):
                families = backend._failover_families_to_try("4a2aa1dbbed382886b0333274e546ba8")
        self.assertEqual(families, ["tcp"])

    def test_failover_keeps_udp_for_local_p2p_while_hub_on(self):
        backend = self._backend()
        with patch.object(backend, "_hub_transport_active", return_value=True):
            with patch.object(backend, "_peer_uses_hub_transport", return_value=False):
                with patch.object(backend, "_has_online_family", return_value=True):
                    with patch("chatxz.core.messaging.physical_lan_reachable", return_value=True):
                        with patch("chatxz.core.messaging.configured_udp_lan_enabled", return_value=True):
                            with patch("chatxz.core.messaging.configured_tcp_lan_enabled", return_value=False):
                                families = backend._failover_families_to_try(
                                    "f1c2ac9061239f7c096701f02969729c"
                                )
        self.assertIn("udp", families)

    def test_on_serial_transport_attached_announces(self):
        backend = self._backend()
        backend.destination = MagicMock()
        with patch.object(backend, "_burst_serial_announce", return_value=3) as burst:
            backend.on_serial_transport_attached(MagicMock(port="/dev/ttyUSB0"))
        burst.assert_called_once()

    def test_queue_send_link_honors_serial_hint_over_udp(self):
        backend = self._backend()
        peer = "f1c2ac9061239f7c096701f02969729c"
        serial_iface = MagicMock()
        udp_iface = MagicMock()
        serial_link = _FakeLink("11" * 16, rtt=0.02, iface=serial_iface)
        udp_link = _FakeLink("22" * 16, rtt=0.002, iface=udp_iface)
        backend.links[serial_link.link_id] = serial_link
        backend.links[udp_link.link_id] = udp_link
        backend._link_peer_hashes[serial_link.link_id] = peer
        backend._link_peer_hashes[udp_link.link_id] = "87a012c46dc2274afccae6fe597b8675"
        backend.peer_links[peer] = serial_link
        backend.peer_transport_resolver = lambda _h: {
            "hash": peer,
            "name": "UBUNTU",
            "via": "serial",
        }
        with patch("chatxz.core.messaging.interface_family", side_effect=lambda i: (
            "serial" if i is serial_iface else "udp"
        )):
            chosen = backend._queue_send_link(peer, link_hint=serial_link)
        self.assertIs(chosen, serial_link)

    def test_queue_send_link_rejects_wrong_peer_udp_link(self):
        backend = self._backend()
        peer = "f1c2ac9061239f7c096701f02969729c"
        udp_iface = MagicMock()
        udp_link = _FakeLink("22" * 16, rtt=0.002, iface=udp_iface)
        backend.links[udp_link.link_id] = udp_link
        backend._link_peer_hashes[udp_link.link_id] = "87a012c46dc2274afccae6fe597b8675"
        backend.peer_transport_resolver = lambda _h: {
            "hash": peer,
            "name": "UBUNTU",
            "via": "serial",
        }
        with patch("chatxz.core.messaging.interface_family", return_value="udp"):
            with patch.object(backend, "_links_for_peer", return_value=[]):
                with patch.object(backend, "_link_for_peer", return_value=None):
                    chosen = backend._queue_send_link(peer, link_hint=udp_link)
        self.assertIsNone(chosen)

    def test_failover_serial_only_peer(self):
        backend = self._backend()
        peer = "f1c2ac9061239f7c096701f02969729c"
        backend.peer_transport_resolver = lambda _h: {
            "hash": peer,
            "name": "UBUNTU",
            "via": "serial",
        }
        with patch.object(backend, "_hub_transport_active", return_value=False):
            with patch.object(backend, "_has_online_family", return_value=True):
                with patch.object(backend, "_serial_transport_ready", return_value=True):
                    with patch("chatxz.core.messaging.physical_lan_reachable", return_value=True):
                        families = backend._failover_families_to_try(peer)
        self.assertEqual(families, ["serial"])

    def test_connect_user_initiated_binds_session_early(self):
        backend = self._backend()
        windows = "87a012c46dc2274afccae6fe597b8675"
        ubuntu = "f1c2ac9061239f7c096701f02969729c"
        backend.active_peer_hash = windows
        backend._session_peer_hash = windows
        with patch.object(backend, "_teardown_other_peer_links", return_value=0) as teardown:
            with patch.object(backend, "_teardown_mismatched_links", return_value=0):
                with patch.object(backend, "_peer_link_active", return_value=False):
                    with patch.object(backend, "_wait_for_identity", return_value=(None, ubuntu)):
                        backend._connect_to_locked(ubuntu, user_initiated=True)
        self.assertEqual(backend.active_peer_hash, ubuntu)
        self.assertEqual(backend._session_peer_hash, ubuntu)
        teardown.assert_called_once()

    def test_session_needs_reconnect_skips_during_connect(self):
        backend = self._backend()
        backend._connect_in_progress = True
        backend.active_peer_hash = "87a012c46dc2274afccae6fe597b8675"
        needs, reason = backend.session_needs_reconnect()
        self.assertFalse(needs)
        self.assertEqual(reason, "")

    def test_reconnect_active_peer_skips_during_connect(self):
        backend = self._backend()
        backend._connect_in_progress = True
        backend.active_peer_hash = "f1c2ac9061239f7c096701f02969729c"
        backend._session_peer_hash = backend.active_peer_hash
        with patch.object(backend, "_serial_transport_ready", return_value=True):
            self.assertFalse(backend.reconnect_active_peer(reason="link dropped"))

    def test_failover_families_serial_via_meta_even_with_lan_up(self):
        backend = self._backend()
        peer = "436ce5fd79d0932d436ce5fd79d0932d"
        backend.peer_transport_resolver = lambda _h: {
            "hash": peer,
            "name": "ARCH",
            "via": "serial",
        }
        with patch.object(backend, "_hub_transport_active", return_value=False):
            with patch.object(backend, "_has_online_family", return_value=True):
                with patch.object(backend, "_serial_transport_ready", return_value=True):
                    with patch("chatxz.core.messaging.physical_lan_reachable", return_value=True):
                        with patch.object(backend, "_serial_faster_than_lan", return_value=False):
                            families = backend._failover_families_to_try(peer)
        self.assertEqual(families, ["serial"])

    def test_expected_transport_serial_when_serial_path_and_out_of_scope_ip(self):
        backend = self._backend()
        peer = "f1c2ac9061239f7c096701f02969729c"
        backend.peer_transport_resolver = lambda _h: {
            "hash": peer,
            "via": "rns",
            "ip": "10.10.10.10",
        }
        with patch.object(backend, "_serial_transport_ready", return_value=True):
            with patch.object(backend, "_peer_has_path_on_family", side_effect=lambda _p, fam: fam == "serial"):
                with patch.object(backend, "_peer_lan_ip_usable", return_value=False):
                    expected = backend._peer_expected_transport_families(peer)
        self.assertEqual(expected, {"serial"})

    def test_prime_serial_path_skips_when_path_cached(self):
        backend = self._backend()
        peer = "436ce5fd79d0932d436ce5fd79d0932d"
        with patch.object(backend, "_serial_transport_ready", return_value=True):
            with patch.object(backend, "_peer_has_path_on_family", return_value=True):
                with patch.object(backend, "_burst_serial_announce") as burst:
                    self.assertTrue(backend._prime_serial_path(peer, timeout_s=8.0))
        burst.assert_not_called()


if __name__ == "__main__":
    unittest.main()