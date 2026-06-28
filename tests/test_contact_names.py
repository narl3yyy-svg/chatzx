"""Contact name persistence and dual-hash save behavior."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatxz.core.contacts import (
    find_contact_by_hash,
    save_contact,
    update_contact_endpoint,
)


class ContactNamePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_custom_name_survives_discovery_update(self):
        save_contact(
            self.tmp,
            "5386ea6054eaaa291518c47732e85127",
            name="My Ubuntu Box",
            ip="10.0.30.101",
            via="lan",
            custom_name=True,
        )
        update_contact_endpoint(
            self.tmp,
            "5386ea6054eaaa291518c47732e85127",
            ip="10.0.30.101",
            name="330s",
        )
        contact = find_contact_by_hash(self.tmp, "5386ea6054eaaa291518c47732e85127")
        self.assertEqual(contact.get("name"), "My Ubuntu Box")
        self.assertTrue(contact.get("custom_name"))

    def test_serial_only_save_does_not_alias_lan_hash(self):
        serial = "178560472b3d6332e27aafb2fef8fe7a"
        contact = save_contact(
            self.tmp,
            serial,
            name="arch",
            via="serial",
            custom_name=True,
        )
        self.assertEqual(contact.get("serial_hash"), serial)
        self.assertNotEqual((contact.get("hash") or "").replace(":", ""), serial)
        self.assertNotEqual((contact.get("lan_hash") or "").replace(":", ""), serial)

    def test_save_serial_then_lan_keeps_distinct_hashes(self):
        save_contact(
            self.tmp,
            "3e212832f1b629ac1bf1442bace4c472",
            name="ubuntu",
            via="serial",
            custom_name=True,
        )
        save_contact(
            self.tmp,
            "5386ea6054eaaa291518c47732e85127",
            name="ubuntu",
            ip="10.0.30.101",
            via="lan",
            custom_name=True,
        )
        contact = find_contact_by_hash(self.tmp, "5386ea6054eaaa291518c47732e85127")
        self.assertEqual(contact.get("lan_hash"), "5386ea6054eaaa291518c47732e85127")
        self.assertEqual(contact.get("serial_hash"), "3e212832f1b629ac1bf1442bace4c472")
        self.assertNotEqual(contact.get("lan_hash"), contact.get("serial_hash"))


if __name__ == "__main__":
    unittest.main()