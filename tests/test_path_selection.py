"""Tests for fastest-path discovery and scope-change peer refresh."""

import os
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatxz.core.discovery import PeerDiscovery

ARCH = "f91235f8a5c69a59295748a78eb38456"
IDENT = "deadbeef" * 4


class PathSelectionTests(unittest.TestCase):
    def test_prefer_lower_rtt_over_transport_rank(self):
        disc = PeerDiscovery()
        serial = {
            "hash": ARCH,
            "identity_hash": IDENT,
            "via": "serial",
            "rtt_avg_ms": 210,
            "last_seen": time.time(),
        }
        lan = {
            "hash": ARCH,
            "identity_hash": IDENT,
            "via": "rns",
            "ip": "10.10.10.37",
            "rtt_avg_ms": 12,
            "last_seen": time.time() - 1,
        }
        with patch("chatxz.core.discovery.serial_discovery_active", return_value=True):
            chosen = disc._prefer_peer(serial, lan, "10.10.10.37")
        self.assertEqual(chosen.get("via"), "rns")

    def test_prefer_serial_when_lan_out_of_scope(self):
        disc = PeerDiscovery()
        serial = {
            "hash": ARCH,
            "identity_hash": IDENT,
            "via": "serial",
            "rtt_avg_ms": 400,
            "last_seen": time.time(),
        }
        lan = {
            "hash": ARCH,
            "identity_hash": IDENT,
            "via": "rns",
            "ip": "10.0.30.112",
            "rtt_avg_ms": 5,
            "last_seen": time.time(),
        }
        with patch("chatxz.core.discovery.serial_discovery_active", return_value=True):
            chosen = disc._prefer_peer(serial, lan, "10.0.5.10")
        self.assertEqual(chosen.get("via"), "serial")

    def test_refresh_paths_drops_out_of_scope_lan_keeps_serial(self):
        disc = PeerDiscovery()
        disc.accept_peers = True
        now = time.time()
        disc.peers[ARCH] = {
            "hash": ARCH,
            "identity_hash": IDENT,
            "name": "arch",
            "via": "serial",
            "last_seen": now,
        }
        lan_hash = "a1" + ARCH[2:]
        disc.peers[lan_hash] = {
            "hash": lan_hash,
            "identity_hash": IDENT,
            "name": "arch",
            "via": "rns",
            "ip": "10.0.30.112",
            "last_seen": now,
        }
        with patch("chatxz.core.discovery.serial_discovery_active", return_value=True):
            removed = disc.refresh_paths_for_scope("10.0.5.10")
            self.assertGreaterEqual(removed, 1)
            peers = disc.get_peers(scope_ip="10.0.5.10")
            self.assertEqual(len(peers), 1)
            self.assertEqual(peers[0].get("via"), "serial")

    def test_get_peers_shows_single_fastest_path(self):
        disc = PeerDiscovery()
        disc.accept_peers = True
        now = time.time()
        disc.peers[ARCH] = {
            "hash": ARCH,
            "identity_hash": IDENT,
            "name": "arch",
            "via": "serial",
            "rtt_avg_ms": 200,
            "last_seen": now,
        }
        alt_hash = "b2" + ARCH[2:]
        disc.peers[alt_hash] = {
            "hash": alt_hash,
            "identity_hash": IDENT,
            "name": "arch",
            "via": "rns",
            "ip": "10.10.10.37",
            "rtt_avg_ms": 15,
            "last_seen": now - 2,
        }
        with patch("chatxz.core.discovery.serial_discovery_active", return_value=True):
            peers = disc.get_peers(scope_ip="10.10.10.37")
        self.assertEqual(len(peers), 1)
        self.assertEqual(peers[0].get("via"), "rns")
        self.assertEqual(peers[0].get("ip"), "10.10.10.37")


class TimingConstantsTests(unittest.TestCase):
    def test_probe_and_beacon_intervals_are_30s(self):
        from chatxz.core.peer_probe import PROBE_INTERVAL_S, PROBE_STALE_S
        from chatxz.core.lan_beacon import LanBeacon

        self.assertEqual(PROBE_INTERVAL_S, 30)
        self.assertEqual(PROBE_STALE_S, 30)
        beacon = LanBeacon(None, "a" * 32)
        self.assertEqual(beacon._interval, 30)


if __name__ == "__main__":
    unittest.main()