"""Tests for discovery TTL and identity supersession."""

import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

import RNS

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatxz.core.discovery import PeerDiscovery, discovery_timeout_s
from chatxz.core.lan_rns import announce_receiving_interface


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

    def test_out_of_scope_lan_ip_reclassified_as_serial(self):
        disc = PeerDiscovery()
        disc.running = True
        disc.accept_peers = True
        peer_hash = bytes.fromhex("f1c2ac9061239f7c096701f02969729c")
        app_data = b'{"app":"chatxz","name":"ubuntu","ip":"10.0.5.10"}'
        lan_iface = MagicMock()
        with patch("chatxz.core.discovery.announce_receiving_interface", return_value=lan_iface):
            with patch("chatxz.core.discovery.interface_family", return_value="udp"):
                with patch("chatxz.core.discovery.serial_discovery_active", return_value=True):
                    with patch("chatxz.utils.platform.discovery_scope_ip", return_value="10.10.10.37"):
                        disc._on_announce(peer_hash, app_data)
        peer = disc.peers.get("f1c2ac9061239f7c096701f02969729c")
        self.assertIsNotNone(peer)
        self.assertEqual(peer["via"], "serial")
        self.assertNotIn("ip", peer)

    def test_sanitize_reclassifies_out_of_scope_as_serial(self):
        disc = PeerDiscovery()
        with patch.object(disc, "_scope_ip", return_value="10.10.10.37"):
            with patch("chatxz.core.discovery.serial_discovery_active", return_value=True):
                result = disc._sanitize_peer_scope({
                    "via": "rns",
                    "ip": "10.0.5.10",
                    "hash": "aa" * 16,
                })
        self.assertEqual(result["via"], "serial")
        self.assertNotIn("ip", result)

    def test_purge_out_of_scope_keeps_serial_peers(self):
        disc = PeerDiscovery()
        serial_hash = "abc" * 8
        lan_hash = "def" * 8
        disc.peers[serial_hash] = {
            "hash": serial_hash,
            "via": "serial",
            "last_seen": time.time(),
        }
        disc.peers[lan_hash] = {
            "hash": lan_hash,
            "via": "rns",
            "ip": "10.0.5.10",
            "last_seen": time.time(),
        }
        removed = disc.purge_out_of_scope("10.10.10.37")
        self.assertEqual(removed, 1)
        self.assertIn(serial_hash, disc.peers)
        self.assertNotIn(lan_hash, disc.peers)


class AnnounceReceivingInterfaceTests(unittest.TestCase):
    def test_prefers_path_table_over_announce_table(self):
        dest = bytes.fromhex("aa" * 16)
        serial_iface = MagicMock()
        lan_iface = MagicMock()
        path_table = {dest: [0, 0, 1, 0, 0, serial_iface]}
        announce_packet = MagicMock(receiving_interface=lan_iface)
        announce_table = {dest: [0, 0, 0, 0, 0, announce_packet]}

        with patch.object(RNS.Transport, "path_table", path_table):
            with patch.object(RNS.Transport, "path_table_lock", MagicMock()):
                with patch.object(RNS.Transport, "announce_table", announce_table):
                    with patch.object(RNS.Transport, "announce_table_lock", MagicMock()):
                        iface = announce_receiving_interface(dest)
        self.assertIs(iface, serial_iface)


if __name__ == "__main__":
    unittest.main()