"""Serial announce rate and discovery broadcast policy (v0.4.0)."""

import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatxz.core.discovery import PeerDiscovery
from chatxz.core.messaging import (
    MessagingBackend,
    SERIAL_ANNOUNCE_BURST_COUNT,
    SERIAL_CONNECT_PRIME_INTERVAL_S,
)


class SerialAnnouncePolicyTests(unittest.TestCase):
    def _backend(self):
        identity = MagicMock()
        return MessagingBackend(identity, "/tmp/chatxz-test")

    def test_default_serial_burst_count_is_one(self):
        self.assertEqual(SERIAL_ANNOUNCE_BURST_COUNT, 1)
        self.assertGreaterEqual(SERIAL_CONNECT_PRIME_INTERVAL_S, 2.0)

    def test_burst_serial_announce_defaults_to_single_packet(self):
        backend = self._backend()
        backend.destination = MagicMock()
        with patch.object(backend, "_serial_transport_ready", return_value=True):
            with patch.object(backend, "_announce_on_interface", return_value=True) as announce:
                with patch(
                    "chatxz.core.messaging.serial_interface_online",
                    return_value=MagicMock(port="/dev/ttyUSB0"),
                ):
                    with patch("chatxz.core.messaging.suppress_offline_lan_transports"):
                        with patch("chatxz.core.messaging.dedupe_serial_interfaces"):
                            with patch("chatxz.core.messaging.prune_dead_serial_interfaces"):
                                sent = backend._burst_serial_announce()
        self.assertEqual(sent, 1)
        self.assertEqual(announce.call_count, 1)

    def test_periodic_announce_includes_serial_when_configured(self):
        backend = self._backend()
        backend.auto_announce = True
        backend.running = True
        backend.announce_interval = 1
        with patch.object(backend, "_has_active_transfer", return_value=False):
            with patch(
                "chatxz.core.messaging.load_settings_interfaces",
                return_value=[{"preset": "serial", "enabled": True}],
            ):
                with patch(
                    "chatxz.core.messaging.lan_discovery_configured",
                    return_value=False,
                ):
                    with patch(
                        "chatxz.core.messaging.configured_serial_enabled",
                        return_value=True,
                    ):
                        with patch.object(backend, "_serial_transport_ready", return_value=True):
                            self.assertTrue(backend._should_periodic_announce())

    def test_announce_loop_sends_single_serial_every_interval(self):
        backend = self._backend()
        backend.auto_announce = True
        backend.running = True
        backend.announce_interval = 1
        calls = {"serial": 0, "silent": 0}

        def serial_announce(**kwargs):
            calls["serial"] += 1
            backend.running = False
            return 1

        with patch.object(backend, "_has_active_transfer", return_value=False):
            with patch(
                "chatxz.core.messaging.load_settings_interfaces",
                return_value=[
                    {"preset": "udp_lan", "enabled": True},
                    {"preset": "serial", "enabled": True},
                ],
            ):
                with patch(
                    "chatxz.core.messaging.lan_discovery_configured",
                    return_value=True,
                ):
                    with patch(
                        "chatxz.core.messaging.configured_serial_enabled",
                        return_value=True,
                    ):
                        with patch.object(backend, "_serial_transport_ready", return_value=True):
                            with patch.object(
                                backend, "_silent_announce", side_effect=lambda **k: calls.__setitem__("silent", calls["silent"] + 1)
                            ):
                                with patch.object(
                                    backend, "_burst_serial_announce", side_effect=serial_announce
                                ):
                                    with patch("chatxz.core.messaging.prune_dead_serial_interfaces"):
                                        with patch("chatxz.core.messaging.time.sleep"):
                                            backend._announce_loop()
        self.assertEqual(calls["silent"], 1)
        self.assertEqual(calls["serial"], 1)

    def test_get_peers_returns_serial_when_lan_out_of_scope(self):
        disc = PeerDiscovery()
        disc.accept_peers = True
        ident = "cafebabe" * 4
        disc.peers["a" * 32] = {
            "hash": "a" * 32,
            "identity_hash": ident,
            "name": "ubuntu",
            "via": "serial",
            "last_seen": time.time(),
        }
        with patch("chatxz.core.discovery.serial_discovery_active", return_value=True):
            peers = disc.get_peers(scope_ip="10.0.5.37")
        self.assertEqual(len(peers), 1)
        self.assertEqual(peers[0].get("via"), "serial")


if __name__ == "__main__":
    unittest.main()