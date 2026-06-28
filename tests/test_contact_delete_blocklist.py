import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatxz.core.contacts import (
    delete_contact,
    find_contact_by_hash,
    list_contacts,
    peer_is_deleted,
    save_contact,
    sync_contact_from_discovery,
)


class ContactDeleteBlocklistTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.lan = "c840613823b77b50ebd25e4c44c58e9b"
        self.serial = "835c597dc551563e1dfc064ec32100e1"

    def test_delete_removes_split_lan_and_serial_files(self):
        save_contact(
            self.tmp,
            self.lan,
            name="330s",
            ip="10.0.30.101",
            via="lan",
            custom_name=True,
        )
        save_contact(
            self.tmp,
            self.serial,
            name="330s",
            via="serial",
            custom_name=True,
        )
        self.assertEqual(len(list_contacts(self.tmp)), 1)
        self.assertTrue(delete_contact(self.tmp, self.lan))
        self.assertEqual(list_contacts(self.tmp), [])
        self.assertTrue(peer_is_deleted(self.tmp, {
            "hash": self.serial,
            "name": "330s",
            "via": "serial",
        }))

    def test_sync_skips_deleted_peer(self):
        save_contact(
            self.tmp,
            self.lan,
            name="330s",
            ip="10.0.30.101",
            via="lan",
            custom_name=True,
        )
        delete_contact(self.tmp, self.lan)
        updated = sync_contact_from_discovery(
            self.tmp,
            {
                "hash": self.serial,
                "name": "330s",
                "via": "serial",
            },
        )
        self.assertIsNone(updated)
        self.assertIsNone(find_contact_by_hash(self.tmp, self.serial))