"""Tests for inbound link peer resolution and adoption."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import RNS
from chatxz.core.messaging import MessagingBackend


class _FakeIdentity:
    def __init__(self, ident_hex):
        self.hash = bytes.fromhex(ident_hex)


class _FakeLink:
    ACTIVE = 2

    def __init__(self, link_id_hex, remote_ident_hex, status=ACTIVE):
        self.link_id = bytes.fromhex(link_id_hex.ljust(32, "0")[:32])
        self._remote = _FakeIdentity(remote_ident_hex)
        self.status = status

    def get_remote_identity(self):
        return self._remote


def _make_backend(peer_resolver=None):
    ident = _FakeIdentity("a" * 32)
    backend = MessagingBackend(
        identity=ident,
        config_dir="/tmp/chatxz-test",
        peer_resolver=peer_resolver,
    )
    backend.my_dest_hash = "b" * 32
    backend.running = True
    return backend


class InboundLinkTests(unittest.TestCase):
    def test_peer_hash_from_link_identity_uses_message_dest(self):
        backend = _make_backend()
        remote_ident = "f687bbff423a220af49f04edb8381ab2"
        link = _FakeLink("11" * 16, remote_ident)
        with patch(
            "chatxz.core.messaging.message_dest_hash_for_identity",
            return_value="4a2aa1dbbed382886b0333274e546ba8",
        ):
            peer = backend._peer_hash_from_link_identity(link)
        self.assertEqual(peer, "4a2aa1dbbed382886b0333274e546ba8")

    def test_resolve_incoming_peer_ignores_stale_discovery_guess(self):
        from chatxz.web.server import ChatWebServer

        server = ChatWebServer.__new__(ChatWebServer)
        server.discovery = MagicMock()
        server.discovery.get_peers.return_value = [
            {
                "hash": "125226cce0253872aaaaaaaaaaaaaaaa",
                "identity_hash": "deadbeefdeadbeefdeadbeefdeadbeef",
                "last_seen": 999,
                "via": "rns",
            }
        ]
        server.messaging = MagicMock()
        server.messaging.dest_hash_for.side_effect = lambda h: (h or "").replace(":", "")
        server.messaging.active_peer_hash = None
        server.messaging.hashes_equivalent = lambda a, b: a == b
        server._is_self_hash = lambda h: False
        server._peer_dest_hash = lambda h: (h or "").replace(":", "")
        server._ui_state = {}
        server.active_peer = None

        resolved = server._resolve_incoming_peer(
            ident_hex="",
            computed_dest="",
            fallback=None,
            link=None,
        )
        self.assertEqual(resolved, "")

    def test_find_active_link_for_peer_matches_remote_identity(self):
        backend = _make_backend()
        remote_ident = "f687bbff423a220af49f04edb8381ab2"
        link = _FakeLink("22" * 16, remote_ident)
        backend.links[link.link_id] = link
        with patch(
            "chatxz.core.messaging.message_dest_hash_for_identity",
            return_value="4a2aa1dbbed382886b0333274e546ba8",
        ):
            found = backend._find_active_link_for_peer("4a2aa1dbbed382886b0333274e546ba8")
        self.assertIs(found, link)

    def test_canonical_connect_hash_maps_identity_alias(self):
        backend = _make_backend()
        ident_hex = "f687bbff423a220af49f04edb8381ab2"
        connect_hex = "4a2aa1dbbed382886b0333274e546ba8"
        backend.register_peer_mapping(connect_hex, ident_hex)
        self.assertEqual(backend.canonical_connect_hash(ident_hex), connect_hex)
        self.assertEqual(backend.canonical_connect_hash(connect_hex), connect_hex)

    def test_notify_link_established_resolves_peer_from_link_identity(self):
        backend = _make_backend()
        remote_ident = "f687bbff423a220af49f04edb8381ab2"
        link = _FakeLink("33" * 16, remote_ident)
        notified = []

        def on_established(peer, link_obj, **kwargs):
            notified.append(peer)

        backend.on_link_established = on_established
        with patch(
            "chatxz.core.messaging.message_dest_hash_for_identity",
            return_value="4a2aa1dbbed382886b0333274e546ba8",
        ):
            backend._notify_link_established(link, peer_hash="unknown")
        self.assertEqual(len(notified), 1)
        self.assertEqual(notified[0], "4a2aa1dbbed382886b0333274e546ba8")
        self.assertEqual(backend.active_peer_hash, "4a2aa1dbbed382886b0333274e546ba8")

    def test_user_disconnect_blocks_session_resume(self):
        backend = _make_backend()
        peer = "4a2aa1dbbed382886b0333274e546ba8"
        backend._session_peer_hash = peer
        backend.mark_user_disconnected(peer)
        self.assertTrue(backend.is_user_disconnected(peer))
        self.assertFalse(backend.resume_session_peer())

    def test_user_initiated_connect_clears_disconnect_flag(self):
        backend = _make_backend()
        peer = "4a2aa1dbbed382886b0333274e546ba8"
        backend.mark_user_disconnected(peer)
        backend.clear_user_disconnected(peer)
        self.assertFalse(backend.is_user_disconnected(peer))

    def test_passive_link_notify_skips_active_promotion(self):
        backend = _make_backend()
        remote_ident = "f687bbff423a220af49f04edb8381ab2"
        link = _FakeLink("44" * 16, remote_ident)
        connect_hex = "4a2aa1dbbed382886b0333274e546ba8"
        backend.mark_user_disconnected(connect_hex)
        notified = []

        def on_established(peer, link_obj, **kwargs):
            notified.append(kwargs)

        backend.on_link_established = on_established
        with patch(
            "chatxz.core.messaging.message_dest_hash_for_identity",
            return_value=connect_hex,
        ):
            backend._notify_link_established(
                link, connect_hex, promote_active=False, passive=True,
            )
        self.assertEqual(len(notified), 1)
        self.assertTrue(notified[0].get("passive"))
        self.assertIsNone(backend.active_peer_hash)

    def test_finish_connect_drains_queue_on_user_connect(self):
        backend = _make_backend()
        peer = "4a2aa1dbbed382886b0333274e546ba8"
        link = _FakeLink("55" * 16, "f687bbff423a220af49f04edb8381ab2")
        backend.links[link.link_id] = link
        backend._link_peer_hashes[link.link_id] = peer
        backend.peer_links[peer] = link
        backend.message_queue = [{
            "type": "text",
            "content": "queued hello",
            "target_hash": peer,
            "msg_id": "abc12345",
        }]
        backend._connect_user_initiated = True
        sent = []

        def fake_send(text, msg_id=None, target_peer=None, link=None, **kwargs):
            sent.append(text)
            from chatxz.core.messaging import ChatMessage
            return ChatMessage("text", text, msg_id=msg_id)

        with patch.object(backend, "send_message", side_effect=fake_send):
            backend._finish_connect(peer, link=link)
        self.assertEqual(sent, ["queued hello"])
        self.assertEqual(backend.message_queue, [])

    def test_consolidate_peer_links_closes_duplicates(self):
        backend = _make_backend()
        peer = "4a2aa1dbbed382886b0333274e546ba8"
        keep = _FakeLink("66" * 16, "f687bbff423a220af49f04edb8381ab2")
        extra = _FakeLink("77" * 16, "f687bbff423a220af49f04edb8381ab2")
        backend.links[keep.link_id] = keep
        backend.links[extra.link_id] = extra
        backend._link_peer_hashes[keep.link_id] = peer
        backend._link_peer_hashes[extra.link_id] = peer
        extra.teardown = MagicMock()
        closed = backend._consolidate_peer_links(peer, keep_link=keep)
        self.assertEqual(closed, 1)
        extra.teardown.assert_called_once()


if __name__ == "__main__":
    unittest.main()