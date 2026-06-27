"""Tests for discovery TTL and identity supersession."""

import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

import RNS

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatxz.core.discovery import PeerDiscovery, discovery_timeout_s
from chatxz.core.lan_rns import (
    announce_receiving_interface,
    restore_serial_path_from_announce,
)


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
        serial_iface = MagicMock()
        with patch("chatxz.core.discovery.announce_packet_receiving_interface", return_value=serial_iface):
            with patch("chatxz.core.discovery.interface_family", return_value="serial"):
                disc._on_announce(peer_hash, app_data, announced_identity=None)
        self.assertIn("436ce5fd79d0932d436ce5fd79d0932d", disc.peers)
        self.assertEqual(disc.peers["436ce5fd79d0932d436ce5fd79d0932d"]["via"], "serial")

    def test_ipless_announce_without_packet_iface_accepted_as_serial(self):
        disc = PeerDiscovery()
        disc.running = True
        disc.accept_peers = True
        peer_hash = bytes.fromhex("986da79e42cd8b10dc6ccb069d978420")
        app_data = b'{"app":"chatxz","name":"ubuntu"}'
        with patch("chatxz.core.discovery.announce_packet_receiving_interface", return_value=None):
            with patch("chatxz.core.discovery.interface_family", return_value=""):
                with patch("chatxz.core.discovery.serial_discovery_active", return_value=True):
                    disc._on_announce(peer_hash, app_data, announced_identity=None)
        self.assertIn("986da79e42cd8b10dc6ccb069d978420", disc.peers)
        self.assertEqual(disc.peers["986da79e42cd8b10dc6ccb069d978420"]["via"], "serial")

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

    def test_out_of_scope_lan_ip_rejected_even_when_usb_up(self):
        disc = PeerDiscovery()
        disc.running = True
        disc.accept_peers = True
        peer_hash = bytes.fromhex("f1c2ac9061239f7c096701f02969729c")
        app_data = b'{"app":"chatxz","name":"ubuntu","ip":"10.0.5.10"}'
        lan_iface = MagicMock()
        with patch("chatxz.core.discovery.announce_packet_receiving_interface", return_value=lan_iface):
            with patch("chatxz.core.discovery.interface_family", return_value="udp"):
                with patch("chatxz.core.discovery.serial_discovery_active", return_value=True):
                    with patch("chatxz.utils.platform.discovery_scope_ip", return_value="10.10.10.37"):
                        disc._on_announce(peer_hash, app_data)
        self.assertNotIn("f1c2ac9061239f7c096701f02969729c", disc.peers)

    def test_serial_announce_with_lan_ip_rejected(self):
        disc = PeerDiscovery()
        disc.running = True
        disc.accept_peers = True
        peer_hash = bytes.fromhex("87a012c46dc2274afccae6fe597b8675")
        app_data = b'{"app":"chatxz","name":"13600k","ip":"10.10.10.2"}'
        serial_iface = MagicMock()
        with patch("chatxz.core.discovery.announce_packet_receiving_interface", return_value=serial_iface):
            with patch("chatxz.core.discovery.interface_family", return_value="serial"):
                with patch("chatxz.utils.platform.discovery_scope_ip", return_value="10.0.5.10"):
                    disc._on_announce(peer_hash, app_data)
        self.assertNotIn("87a012c46dc2274afccae6fe597b8675", disc.peers)

    def test_ipless_rns_peer_rejected_in_sanitize(self):
        disc = PeerDiscovery()
        with patch.object(disc, "_scope_ip", return_value="10.0.5.10"):
            with patch("chatxz.core.discovery.serial_discovery_active", return_value=True):
                result = disc._sanitize_peer_scope({
                    "via": "rns",
                    "hash": "aa" * 16,
                })
        self.assertIsNone(result)

    def test_in_scope_lan_ip_stays_rns(self):
        disc = PeerDiscovery()
        disc.running = True
        disc.accept_peers = True
        peer_hash = bytes.fromhex("87a012c46dc2274afccae6fe597b8675")
        app_data = b'{"app":"chatxz","name":"13600k","ip":"10.10.10.2"}'
        lan_iface = MagicMock()
        with patch("chatxz.core.discovery.announce_packet_receiving_interface", return_value=lan_iface):
            with patch("chatxz.core.discovery.interface_family", return_value="udp"):
                with patch("chatxz.core.discovery.serial_discovery_active", return_value=True):
                    with patch("chatxz.utils.platform.discovery_scope_ip", return_value="10.10.10.37"):
                        disc._on_announce(peer_hash, app_data)
        peer = disc.peers.get("87a012c46dc2274afccae6fe597b8675")
        self.assertIsNotNone(peer)
        self.assertEqual(peer.get("via"), "rns")
        self.assertEqual(peer.get("ip"), "10.10.10.2")

    def test_sanitize_rejects_out_of_scope_lan(self):
        disc = PeerDiscovery()
        with patch.object(disc, "_scope_ip", return_value="10.10.10.37"):
            with patch("chatxz.core.discovery.serial_discovery_active", return_value=True):
                result = disc._sanitize_peer_scope({
                    "via": "rns",
                    "ip": "10.0.5.10",
                    "hash": "aa" * 16,
                })
        self.assertIsNone(result)

    def test_sanitize_rejects_ipless_rns_when_usb_up(self):
        disc = PeerDiscovery()
        with patch.object(disc, "_scope_ip", return_value="10.0.5.10"):
            with patch("chatxz.core.discovery.serial_discovery_active", return_value=True):
                result = disc._sanitize_peer_scope({
                    "via": "rns",
                    "hash": "aa" * 16,
                    "name": "13600k",
                })
        self.assertIsNone(result)

    def test_bridged_serial_announce_with_ip_rejected(self):
        disc = PeerDiscovery()
        disc.running = True
        disc.accept_peers = True
        peer_hash = bytes.fromhex("87a012c46dc2274afccae6fe597b8675")
        app_data = b'{"app":"chatxz","name":"13600k","ip":"10.10.10.2"}'
        serial_iface = MagicMock()
        with patch("chatxz.core.discovery.announce_packet_receiving_interface", return_value=serial_iface):
            with patch("chatxz.core.discovery.interface_family", return_value="serial"):
                with patch("chatxz.utils.platform.discovery_scope_ip", return_value="10.0.5.10"):
                    disc._on_announce(peer_hash, app_data)
        self.assertNotIn("87a012c46dc2274afccae6fe597b8675", disc.peers)

    def test_beacon_rejects_out_of_scope_source_ip(self):
        disc = PeerDiscovery()
        disc.running = True
        disc.accept_peers = True
        data = {
            "app": "chatxz",
            "hash": "87a012c46dc2274afccae6fe597b8675",
            "identity_hash": "a" * 32,
            "name": "13600k",
            "ip": "10.10.10.2",
            "port": 8742,
            "pubkey": "dGVzdA==",
        }
        with patch("chatxz.core.discovery.PeerDiscovery._scope_ip", return_value="10.0.5.10"):
            with patch("chatxz.core.peer_identity.peer_record_from_beacon") as rec:
                rec.return_value = {
                    "hash": "87a012c46dc2274afccae6fe597b8675",
                    "name": "13600k",
                    "via": "beacon",
                    "ip": "10.10.10.2",
                }
                ok = disc._on_beacon(data, "b" * 32, source_ip="10.10.10.2")
        self.assertFalse(ok)

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
    def test_prefers_serial_packet_iface_over_lan_path_table(self):
        dest = bytes.fromhex("aa" * 16)
        serial_iface = MagicMock()
        lan_iface = MagicMock()
        path_table = {dest: [0, 0, 1, 0, 0, lan_iface]}
        announce_packet = MagicMock(receiving_interface=serial_iface)
        announce_table = {dest: [0, 0, 0, 0, 0, announce_packet]}

        with patch("chatxz.core.lan_rns.interface_family", side_effect=lambda i: (
            "serial" if i is serial_iface else "udp"
        )):
            with patch.object(RNS.Transport, "path_table", path_table):
                with patch.object(RNS.Transport, "path_table_lock", MagicMock()):
                    with patch.object(RNS.Transport, "announce_table", announce_table):
                        with patch.object(RNS.Transport, "announce_table_lock", MagicMock()):
                            iface = announce_receiving_interface(dest)
        self.assertIs(iface, serial_iface)

    def test_restore_serial_path_from_usb_announce(self):
        dest = bytes.fromhex("bb" * 16)
        dest_hex = dest.hex()
        serial_iface = MagicMock()
        lan_iface = MagicMock()
        path_table = {dest: [0, 0, 1, 0, 0, lan_iface]}
        announce_packet = MagicMock(receiving_interface=serial_iface)
        announce_table = {dest: [0, 0, 0, 0, 0, announce_packet]}

        with patch("chatxz.core.lan_rns.interface_family", side_effect=lambda i: (
            "serial" if i is serial_iface else "udp"
        )):
            with patch("chatxz.core.lan_rns.interface_is_healthy", return_value=True):
                with patch.object(RNS.Transport, "path_table", path_table):
                    with patch.object(RNS.Transport, "path_table_lock", MagicMock()):
                        with patch.object(RNS.Transport, "announce_table", announce_table):
                            with patch.object(RNS.Transport, "announce_table_lock", MagicMock()):
                                restored = restore_serial_path_from_announce(dest_hex)
        self.assertIs(restored, serial_iface)
        self.assertIs(path_table[dest][5], serial_iface)


if __name__ == "__main__":
    unittest.main()