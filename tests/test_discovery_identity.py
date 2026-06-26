"""Tests for discovery TTL and identity supersession."""

import os
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatxz.core.discovery import PeerDiscovery, discovery_timeout_s


class DiscoveryIdentityTests(unittest.TestCase):
    def test_discovery_ttl_is_five_minutes(self):
        self.assertEqual(discovery_timeout_s(), 300)

    def test_evict_superseded_peer_on_same_ip_new_hash(self):
        disc = PeerDiscovery()
        disc.accept_peers = True
        disc.peers["oldhash123456789012345678901234"] = {
            "hash": "oldhash123456789012345678901234",
            "name": "ubuntu",
            "ip": "10.0.30.101",
            "port": 8742,
            "last_seen": time.time(),
            "via": "beacon",
        }
        disc._store_peer({
            "hash": "newhash123456789012345678901234",
            "name": "ubuntu",
            "ip": "10.0.30.101",
            "port": 8742,
            "last_seen": time.time(),
            "via": "beacon",
        })
        self.assertEqual(len(disc.peers), 1)
        self.assertIn("newhash123456789012345678901234", disc.peers)

    def test_purge_hashes_removes_matching_entries(self):
        disc = PeerDiscovery()
        disc.peers["deadbeefdeadbeefdeadbeefdeadbeef"] = {
            "hash": "deadbeefdeadbeefdeadbeefdeadbeef",
            "identity_hash": "cafebabecafebabecafebabecafebabe",
            "last_seen": time.time(),
        }
        removed = disc.purge_hashes({
            "deadbeefdeadbeefdeadbeefdeadbeef",
            "cafebabecafebabecafebabecafebabe",
        })
        self.assertEqual(removed, 1)
        self.assertEqual(len(disc.peers), 0)

    def test_get_peers_keeps_newest_hash_per_ip(self):
        disc = PeerDiscovery()
        disc.accept_peers = True
        now = time.time()
        disc.peers["oldhash123456789012345678901234"] = {
            "hash": "oldhash123456789012345678901234",
            "name": "ubuntu",
            "ip": "10.0.30.101",
            "last_seen": now - 5,
            "via": "rns",
        }
        disc.peers["newhash123456789012345678901234"] = {
            "hash": "newhash123456789012345678901234",
            "name": "ubuntu",
            "ip": "10.0.30.101",
            "last_seen": now,
            "via": "beacon",
        }
        peers = disc.get_peers()
        self.assertEqual(len(peers), 1)
        self.assertEqual(peers[0]["hash"], "newhash123456789012345678901234")

    def test_rns_announce_evicts_same_name_peer(self):
        from unittest.mock import patch

        disc = PeerDiscovery()
        disc.accept_peers = True
        disc.peers["8503195b200ad31536053584f86c9908"] = {
            "hash": "8503195b200ad31536053584f86c9908",
            "name": "ubuntu",
            "ip": "10.10.100.4",
            "last_seen": time.time(),
            "via": "beacon",
        }
        with patch("chatxz.core.discovery.PeerDiscovery._scope_ip", return_value=None):
            disc._store_peer({
                "hash": "a68cdfa88742c19a1edec7c2ae021f25",
                "name": "ubuntu",
                "last_seen": time.time(),
                "via": "rns",
            })
        self.assertEqual(len(disc.peers), 1)
        self.assertIn("a68cdfa88742c19a1edec7c2ae021f25", disc.peers)

    def test_ipless_announce_discovered_as_serial_without_cached_path(self):
        disc = PeerDiscovery()
        disc.running = True
        disc.accept_peers = True
        peer_hash = bytes.fromhex("436ce5fd79d0932d436ce5fd79d0932d")
        app_data = b'{"app":"chatxz","name":"ARCH"}'
        with patch("chatxz.core.discovery.serial_discovery_active", return_value=True):
            with patch("chatxz.core.lan_rns.peer_path_on_family", return_value=None):
                disc._on_announce(peer_hash, app_data, announced_identity=None)
        self.assertIn("436ce5fd79d0932d436ce5fd79d0932d", disc.peers)
        self.assertEqual(disc.peers["436ce5fd79d0932d436ce5fd79d0932d"]["via"], "serial")

    def test_stale_peer_pruned_after_ttl(self):
        disc = PeerDiscovery()
        disc.accept_peers = True
        disc.peers["abcdabcdabcdabcdabcdabcdabcdabcd"] = {
            "hash": "abcdabcdabcdabcdabcdabcdabcdabcd",
            "name": "peer",
            "last_seen": time.time() - discovery_timeout_s() - 1,
            "via": "rns",
        }
        peers = disc.get_peers()
        self.assertEqual(peers, [])


if __name__ == "__main__":
    unittest.main()