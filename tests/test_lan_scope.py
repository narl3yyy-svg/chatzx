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


    def test_beacon_rejects_cross_subnet_peer_ip(self):
        disc = PeerDiscovery()
        disc.accept_peers = True
        disc.running = True
        from unittest.mock import patch

        beacon = {
            "app": "chatxz",
            "hash": "c" * 32,
            "name": "ARCH",
            "ip": "10.0.30.112",
            "port": 8742,
            "identity_hash": "d" * 32,
            "pubkey": "dGVzdA==",
        }
        with patch("chatxz.utils.platform.discovery_scope_ip", return_value="10.10.100.4"):
            accepted = disc._on_beacon(
                beacon,
                my_dest_hash="a" * 32,
                my_identity_hash="b" * 32,
                source_ip="10.10.100.12",
            )
        self.assertFalse(accepted)
        self.assertEqual(len(disc.peers), 0)

    def test_store_peer_rejects_cross_subnet_when_scoped(self):
        disc = PeerDiscovery()
        from unittest.mock import patch

        with patch("chatxz.core.discovery.serial_discovery_active", return_value=False):
            with patch("chatxz.core.discovery.PeerDiscovery._scope_ip", return_value="10.10.10.37"):
                disc._store_peer({
                    "hash": "f" * 32,
                    "ip": "10.0.5.10",
                    "name": "UBUNTU",
                    "via": "rns",
                    "last_seen": __import__("time").time(),
                })
        self.assertEqual(len(disc.peers), 0)

    def test_store_peer_reclassifies_cross_subnet_as_serial_when_usb_up(self):
        disc = PeerDiscovery()
        from unittest.mock import patch

        peer_hash = "g" * 32
        with patch("chatxz.core.discovery.serial_discovery_active", return_value=True):
            with patch("chatxz.core.discovery.PeerDiscovery._scope_ip", return_value="10.0.30.112"):
                ok = disc._store_peer({
                    "hash": peer_hash,
                    "ip": "10.10.10.2",
                    "name": "13600k",
                    "via": "rns",
                    "last_seen": __import__("time").time(),
                })
        self.assertTrue(ok)
        peer = disc.peers.get(peer_hash) or {}
        self.assertEqual(peer.get("via"), "serial")
        self.assertNotIn("ip", peer)

    def test_store_peer_drops_existing_peer_on_subnet_move(self):
        disc = PeerDiscovery()
        now = __import__("time").time()
        peer_hash = "e" * 32
        disc.peers[peer_hash] = {
            "hash": peer_hash,
            "ip": "10.0.5.37",
            "name": "ARCH",
            "last_seen": now,
            "via": "beacon",
        }
        evicted = []
        disc.on_peer_evicted = lambda removed, new: evicted.append(removed)
        from unittest.mock import patch

        with patch("chatxz.core.discovery.PeerDiscovery._scope_ip", return_value="10.0.5.10"):
            disc._store_peer({
                "hash": peer_hash,
                "ip": "10.10.10.37",
                "name": "ARCH",
                "via": "rns",
                "last_seen": now,
            })
        self.assertNotIn(peer_hash, disc.peers)
        self.assertEqual(evicted, [[peer_hash]])

    def test_ipless_peer_allowed_when_serial_active(self):
        from unittest.mock import patch

        disc = PeerDiscovery()
        with patch("chatxz.core.discovery.serial_discovery_active", return_value=True):
            with patch("chatxz.core.discovery.PeerDiscovery._scope_ip", return_value="10.0.5.37"):
                disc._store_peer({
                    "hash": "s" * 32,
                    "name": "SERIALPEER",
                    "via": "rns",
                    "last_seen": __import__("time").time(),
                })
        self.assertEqual(len(disc.peers), 1)

    def test_ipless_rns_peer_stored_as_serial_via(self):
        from unittest.mock import patch

        disc = PeerDiscovery()
        peer_hash = "f" * 32
        with patch("chatxz.core.discovery.serial_discovery_active", return_value=True):
            with patch.object(disc, "_scope_ip", return_value="10.0.5.10"):
                ok = disc._store_peer({
                    "hash": peer_hash,
                    "name": "UBUNTU",
                    "via": "serial",
                    "last_seen": __import__("time").time(),
                })
        self.assertTrue(ok)
        self.assertEqual(disc.peers[peer_hash].get("via"), "serial")
        self.assertNotIn("ip", disc.peers[peer_hash])

    def test_serial_via_peer_bypasses_scope(self):
        from unittest.mock import patch

        disc = PeerDiscovery()
        with patch("chatxz.core.discovery.PeerDiscovery._scope_ip", return_value="10.0.30.2"):
            disc._store_peer({
                "hash": "t" * 32,
                "name": "REMOTE",
                "via": "serial",
                "last_seen": __import__("time").time(),
            })
        self.assertIn("t" * 32, disc.peers)

    def test_scoped_peers_hide_ipless_entries_without_serial(self):
        from unittest.mock import patch

        disc = PeerDiscovery()
        disc.accept_peers = True
        now = __import__("time").time()
        disc.peers["x"] = {
            "hash": "x" * 32,
            "name": "NOIP",
            "last_seen": now,
            "via": "rns",
        }
        disc.peers["y"] = {
            "hash": "y" * 32,
            "ip": "10.0.5.10",
            "name": "UBUNTU",
            "last_seen": now,
            "via": "beacon",
        }
        with patch("chatxz.core.discovery.serial_discovery_active", return_value=False):
            scoped = disc.get_peers(scope_ip="10.0.5.37")
        names = {p.get("name") for p in scoped}
        self.assertIn("UBUNTU", names)
        self.assertNotIn("NOIP", names)

    def test_lan_broadcast_uses_pinned_ip(self):
        from unittest.mock import patch
        from chatxz.utils.platform import lan_broadcast, set_lan_interface_preference

        set_lan_interface_preference("10.10.100.4")
        with patch("chatxz.utils.platform.list_network_interfaces", return_value=[]):
            with patch("chatxz.utils.platform.lan_ip", return_value=None):
                self.assertEqual(lan_broadcast(), "10.10.100.255")
        set_lan_interface_preference("")


if __name__ == "__main__":
    unittest.main()