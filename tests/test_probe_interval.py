"""Tests for configurable probe interval and LAN RTT probing."""

import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatxz.core.peer_identity import peer_record_from_beacon
from chatxz.core.peer_probe import (
    PROBE_PACKET_MIN_BYTES,
    clamp_probe_interval,
    clamp_serial_probe_interval,
    link_rtt_ms,
    probe_packet_bytes,
)


class ProbeIntervalTests(unittest.TestCase):
    def test_clamp_probe_interval_bounds(self):
        self.assertEqual(clamp_probe_interval(0), 0)
        self.assertEqual(clamp_probe_interval(3), 3)
        self.assertEqual(clamp_probe_interval(30), 30)
        self.assertEqual(clamp_probe_interval(99999), 18000)
        self.assertEqual(clamp_probe_interval("bad"), 30)

    def test_clamp_serial_probe_interval_minimum(self):
        self.assertEqual(clamp_serial_probe_interval(0), 0)
        self.assertEqual(clamp_serial_probe_interval(1), 3)
        self.assertEqual(clamp_serial_probe_interval(3), 3)
        self.assertEqual(clamp_serial_probe_interval(30), 30)

    def test_probe_packet_bytes_fixed_minimum(self):
        self.assertEqual(probe_packet_bytes(), PROBE_PACKET_MIN_BYTES)

    def test_peer_record_from_beacon_without_identity_registration(self):
        data = {
            "app": "chatxz",
            "hash": "a" * 32,
            "name": "android",
            "ip": "10.0.30.55",
            "port": 8742,
        }
        with patch("chatxz.core.peer_identity.register_beacon_identity", return_value=""):
            peer = peer_record_from_beacon(data)
        self.assertIsNotNone(peer)
        self.assertEqual(peer.get("hash"), "a" * 32)
        self.assertEqual(peer.get("ip"), "10.0.30.55")
        self.assertEqual(peer.get("via"), "beacon")

    def test_link_rtt_ms_from_active_link(self):
        import RNS

        messaging = MagicMock()
        link = MagicMock()
        link.status = RNS.Link.ACTIVE
        link.rtt = 0.016
        messaging._link_for_peer.return_value = link
        rtt = link_rtt_ms(messaging, "b" * 32)
        self.assertEqual(rtt, 16)

    def test_probe_runs_despite_fresh_last_seen(self):
        """RTT probes use last_rtt_probe_at, not announce last_seen."""
        from chatxz.web.server import ChatWebServer

        server = ChatWebServer.__new__(ChatWebServer)
        server.config_dir = "/tmp"
        server.websockets = set()
        server._loop = None
        server.discovery = MagicMock()
        server.discovery.accept_peers = True
        server.discovery.peers = {
            "c" * 32: {
                "hash": "c" * 32,
                "via": "rns",
                "ip": "10.0.30.10",
                "last_seen": time.time(),
                "last_rtt_probe_at": 0,
            }
        }
        server.messaging = None
        server.load_settings = lambda: {"probe_interval_s": 30}

        with patch("chatxz.web.server.ChatWebServer._schedule_peers_broadcast"):
            with patch("chatxz.core.peer_probe.probe_udp_peer", return_value=12) as udp_probe:
                with patch.object(server.discovery, "purge_stale_probes", return_value=0):
                    server.discovery.update_peer_probe = MagicMock()
                    server._probe_discovered_peers()
        udp_probe.assert_called_once()
        args, kwargs = udp_probe.call_args
        self.assertEqual(args[0], "10.0.30.10")
        self.assertEqual(kwargs.get("timeout_s"), 1.5)
        self.assertEqual(kwargs.get("packet_bytes"), PROBE_PACKET_MIN_BYTES)
        server.discovery.update_peer_probe.assert_called_once()


if __name__ == "__main__":
    unittest.main()