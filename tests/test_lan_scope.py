"""Tests for LAN subnet scope matching."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatxz.core.discovery import PeerDiscovery
from chatxz.utils.lan_scope import same_lan_scope


class LanScopeTests(unittest.TestCase):
    def test_slash24_match(self):
        self.assertTrue(same_lan_scope("10.10.100.12", "10.10.100.4"))

    def test_10_slash8_does_not_match(self):
        self.assertFalse(same_lan_scope("10.0.30.112", "10.10.100.4"))
        self.assertFalse(same_lan_scope("10.0.30.101", "10.10.100.12"))

    def test_172_slash12_match(self):
        self.assertTrue(same_lan_scope("172.17.13.110", "172.17.121.37"))

    def test_discovery_scoped_to_primary_lan(self):
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