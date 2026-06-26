"""Serial link tuning for reliable RNS file transfers at low baud rates."""

from contextlib import contextmanager

import RNS

from chatxz.core.lan_rns import interface_family

SERIAL_LINK_MTU = 500
SERIAL_TRAFFIC_TIMEOUT_FACTOR = 48
SERIAL_RESOURCE_WINDOW = 2
SERIAL_RESOURCE_WINDOW_MAX = 3
SERIAL_RESOURCE_WINDOW_MIN = 1
SERIAL_PART_TIMEOUT_FACTOR = 10.0
SERIAL_MIN_TRANSFER_TIMEOUT_S = 180.0
SERIAL_TIMEOUT_PER_BYTE_S = 0.0012  # ~10 bits/byte at 57600 with margin


def serial_baud_from_interface(iface):
    if not iface:
        return 57600
    for attr in ("speed", "bitrate", "baud", "baudrate"):
        val = getattr(iface, attr, None)
        if val:
            try:
                return max(1200, int(val))
            except Exception:
                pass
    return 57600


def is_serial_interface(iface):
    return interface_family(iface) == "serial"


def serial_transfer_timeout_s(file_size, baud):
    size = max(int(file_size or 0), 4096)
    baud = max(int(baud or 57600), 1200)
    est = size * SERIAL_TIMEOUT_PER_BYTE_S * (57600 / baud)
    return max(SERIAL_MIN_TRANSFER_TIMEOUT_S, est * 2.5)


SERIAL_LINK_ESTABLISH_TIMEOUT_S = 22


@contextmanager
def boost_serial_establishment_timeout(timeout_s=None):
    """Raise RNS link-establishment timeouts for slow serial handshakes."""
    limit = timeout_s or SERIAL_LINK_ESTABLISH_TIMEOUT_S
    old_default = RNS.Reticulum.DEFAULT_PER_HOP_TIMEOUT
    old_per_hop = RNS.Link.ESTABLISHMENT_TIMEOUT_PER_HOP
    try:
        RNS.Reticulum.DEFAULT_PER_HOP_TIMEOUT = limit
        RNS.Link.ESTABLISHMENT_TIMEOUT_PER_HOP = limit
        yield
    finally:
        RNS.Reticulum.DEFAULT_PER_HOP_TIMEOUT = old_default
        RNS.Link.ESTABLISHMENT_TIMEOUT_PER_HOP = old_per_hop


def tune_serial_link(link, iface=None):
    """Prepare a link for slow serial bulk transfers (window=2, long timeouts)."""
    if not link:
        return
    attached = iface or getattr(link, "attached_interface", None)
    if not is_serial_interface(attached):
        return
    try:
        link.traffic_timeout_factor = SERIAL_TRAFFIC_TIMEOUT_FACTOR
        link.last_resource_window = SERIAL_RESOURCE_WINDOW
        mtu = min(int(getattr(link, "mtu", 500) or 500), SERIAL_LINK_MTU)
        if mtu < getattr(link, "mtu", 500):
            link.mtu = mtu
            if hasattr(link, "update_mdu"):
                link.update_mdu()
    except Exception:
        pass


def tune_outgoing_resource(resource, iface=None):
    """Throttle sender window and timeouts on serial links."""
    if not resource:
        return
    attached = iface or getattr(getattr(resource, "link", None), "attached_interface", None)
    if not is_serial_interface(attached):
        return
    baud = serial_baud_from_interface(attached)
    try:
        resource.window = SERIAL_RESOURCE_WINDOW
        resource.window_max = SERIAL_RESOURCE_WINDOW_MAX
        resource.window_min = SERIAL_RESOURCE_WINDOW_MIN
        resource.window_flexibility = 1
        resource.part_timeout_factor = SERIAL_PART_TIMEOUT_FACTOR
        resource.timeout_factor = SERIAL_TRAFFIC_TIMEOUT_FACTOR
        size = int(getattr(resource, "total_size", 0) or getattr(resource, "size", 0) or 0)
        resource.timeout = serial_transfer_timeout_s(size, baud)
        resource.max_retries = max(int(getattr(resource, "max_retries", 5) or 5), 8)
    except Exception:
        pass


def tune_incoming_resource(resource, iface=None):
    """Receiver requests one part at a time on slow serial links."""
    if not resource:
        return
    link = getattr(resource, "link", None)
    attached = iface or getattr(link, "attached_interface", None)
    if not is_serial_interface(attached):
        return
    baud = serial_baud_from_interface(attached)
    try:
        resource.window = SERIAL_RESOURCE_WINDOW
        resource.window_max = SERIAL_RESOURCE_WINDOW_MAX
        resource.window_min = SERIAL_RESOURCE_WINDOW_MIN
        resource.window_flexibility = 1
        resource.part_timeout_factor = SERIAL_PART_TIMEOUT_FACTOR
        resource.timeout_factor = SERIAL_TRAFFIC_TIMEOUT_FACTOR
        size = int(getattr(resource, "size", 0) or getattr(resource, "total_size", 0) or 0)
        resource.timeout = serial_transfer_timeout_s(size, baud)
        resource.max_retries = max(int(getattr(resource, "max_retries", 5) or 5), 8)
        if link:
            link.last_resource_window = SERIAL_RESOURCE_WINDOW
    except Exception:
        pass