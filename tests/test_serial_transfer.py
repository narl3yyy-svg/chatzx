"""Tests for serial file-transfer tuning."""

import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatxz.core.serial_transfer import (
    SERIAL_RESOURCE_WINDOW,
    SERIAL_TRAFFIC_TIMEOUT_FACTOR,
    serial_transfer_timeout_s,
    tune_incoming_resource,
    tune_outgoing_resource,
    tune_serial_link,
)


class SerialTransferTests(unittest.TestCase):
    def test_serial_transfer_timeout_scales_with_size(self):
        small = serial_transfer_timeout_s(4096, 57600)
        large = serial_transfer_timeout_s(5 * 1024 * 1024, 57600)
        self.assertGreater(large, small)
        self.assertGreaterEqual(small, 90.0)

    def test_tune_serial_link_sets_window_and_timeout_factor(self):
        link = MagicMock()
        link.mtu = 1064
        iface = MagicMock()
        iface.speed = 57600
        with unittest.mock.patch(
            "chatxz.core.serial_transfer.is_serial_interface",
            return_value=True,
        ):
            tune_serial_link(link, iface)
        self.assertEqual(link.traffic_timeout_factor, SERIAL_TRAFFIC_TIMEOUT_FACTOR)
        self.assertEqual(link.last_resource_window, SERIAL_RESOURCE_WINDOW)
        self.assertLessEqual(link.mtu, 500)

    def test_tune_outgoing_resource_window(self):
        link = MagicMock()
        link.attached_interface = MagicMock(speed=57600)
        resource = MagicMock()
        resource.link = link
        resource.total_size = 1024 * 1024
        with unittest.mock.patch(
            "chatxz.core.serial_transfer.is_serial_interface",
            return_value=True,
        ):
            tune_outgoing_resource(resource, link.attached_interface)
        self.assertEqual(resource.window, 3)
        self.assertEqual(resource.window_max, 4)

    def test_tune_incoming_resource_window(self):
        link = MagicMock()
        link.attached_interface = MagicMock(speed=115200)
        resource = MagicMock()
        resource.link = link
        resource.size = 500000
        with unittest.mock.patch(
            "chatxz.core.serial_transfer.is_serial_interface",
            return_value=True,
        ):
            tune_incoming_resource(resource, link.attached_interface)
        self.assertEqual(resource.window, 3)
        self.assertEqual(link.last_resource_window, 3)


class TransferCancelTests(unittest.TestCase):
    def test_cancel_incoming_marks_transfer_cancelled(self):
        from chatxz.core.messaging import MessagingBackend, MESSAGE_TYPE_TRANSFER_CANCEL

        ident = MagicMock()
        ident.hash = bytes.fromhex("a" * 32)
        backend = MessagingBackend(identity=ident, config_dir="/tmp/chatxz-cancel-test")
        backend.running = True
        link = MagicMock()
        link.link_id = b"\x01" * 16
        link.incoming_resources = [MagicMock()]
        backend._pending_files[link.link_id] = [
            {"msg_id": "abc123", "file_name": "big.bin"},
        ]
        from chatxz.core.messaging import ChatMessage
        backend._pending_files[link.link_id] = [
            ChatMessage("file", "", file_name="big.bin", msg_id="abc123"),
        ]
        with unittest.mock.patch.object(backend, "_emit_progress") as emit:
            ok = backend._cancel_incoming_resources(link, transfer_id="abc123")
        self.assertTrue(ok)
        self.assertIn("abc123", backend._cancelled_transfers)
        emit.assert_called()
        self.assertEqual(backend._pending_files.get(link.link_id), [])

    def test_transfer_cancel_message_type_constant(self):
        from chatxz.core.messaging import MESSAGE_TYPE_TRANSFER_CANCEL
        self.assertEqual(MESSAGE_TYPE_TRANSFER_CANCEL, "__transfer_cancel")


if __name__ == "__main__":
    unittest.main()