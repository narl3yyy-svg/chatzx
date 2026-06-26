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
        self.assertEqual(resource.window, 2)
        self.assertEqual(resource.window_max, 3)

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
        self.assertEqual(resource.window, 2)
        self.assertEqual(link.last_resource_window, 2)


if __name__ == "__main__":
    unittest.main()