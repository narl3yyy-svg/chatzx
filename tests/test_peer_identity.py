"""Tests for canonical connect hash resolution."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatxz.core.discovery import message_dest_hash_for_identity, normalize_hash


class PeerIdentityTests(unittest.TestCase):
    def test_connect_hash_differs_from_identity_hex(self):
        try:
            import RNS
        except ImportError:
            self.skipTest("RNS not installed")
        ident = RNS.Identity()
        identity_hex = normalize_hash(RNS.hexrep(ident.hash))
        connect = message_dest_hash_for_identity(ident)
        self.assertTrue(connect)
        self.assertNotEqual(connect, identity_hex)

    def test_discovery_dedupe_prefers_rns_over_beacon(self):
        from chatxz.core.discovery import PeerDiscovery
        import time
        disc = PeerDiscovery()
        disc.accept_peers = True
        now = time.time()
        disc.peers["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"] = {
            "hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "name": "aaaaaaaa",
            "ip": "10.0.30.101",
            "last_seen": now,
            "via": "beacon",
        }
        disc.peers["bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"] = {
            "hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "name": "ubuntu",
            "ip": "10.0.30.101",
            "last_seen": now - 1,
            "via": "rns",
            "pubkey": "dGVzdA==",
        }
        peers = disc.get_peers(scope_ip="10.0.30.112")
        self.assertEqual(len(peers), 1)
        self.assertEqual(peers[0]["hash"], "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")


if __name__ == "__main__":
    unittest.main()