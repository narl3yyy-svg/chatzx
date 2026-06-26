"""Shared LAN subnet matching for discovery, contacts, and UI."""


def same_lan_scope(ip_a, ip_b):
    """True when two IPv4 addresses are on the same LAN discovery scope.

    Uses /24 by default. Private ranges also match within RFC1918 supernets:
    - 172.16.0.0/12 (172.16.x – 172.31.x)
    - 192.168.0.0/16

    10.0.0.0/8 is NOT treated as one network — 10.0.30.x and 10.10.100.x differ.
    """
    if not ip_a or not ip_b:
        return False
    parts_a = str(ip_a).split(".")
    parts_b = str(ip_b).split(".")
    if len(parts_a) != 4 or len(parts_b) != 4:
        return False
    if parts_a[:3] == parts_b[:3]:
        return True
    try:
        a0, a1 = int(parts_a[0]), int(parts_a[1])
        b0, b1 = int(parts_b[0]), int(parts_b[1])
    except ValueError:
        return False
    if a0 == b0 == 172 and 16 <= a1 <= 31 and 16 <= b1 <= 31:
        return True
    if a0 == b0 == 192 and a1 == b1 == 168:
        return True
    return False