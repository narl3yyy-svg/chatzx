"""LAN helpers for RNS UDP announces (unicast supplements broadcast on Android/Wi-Fi)."""

import socket

import RNS

from chatxz.utils.platform import is_android, lan_ip, list_network_interfaces

RNS_PORT = 4242


def _subnet_unicast_targets(peer_ip=None):
    ip = lan_ip()
    targets = []
    if peer_ip and peer_ip not in targets:
        targets.append(peer_ip)
    if ip:
        parts = ip.split(".")
        if len(parts) == 4:
            base = f"{parts[0]}.{parts[1]}.{parts[2]}"
            my_host = parts[3]
            for i in range(1, 255):
                host = f"{base}.{i}"
                if host != my_host and host not in targets:
                    targets.append(host)
    return targets


def unicast_announce_packet(packet, peer_ip=None, port=RNS_PORT, subnet_probe=None):
    """Send a packed RNS announce directly to peer IP and/or subnet hosts."""
    if packet is None:
        return 0
    if not getattr(packet, "packed", False):
        packet.pack()
    data = getattr(packet, "raw", None)
    if not data:
        return 0

    if subnet_probe is None:
        subnet_probe = is_android()

    targets = []
    if peer_ip:
        targets.append(peer_ip)
    if subnet_probe:
        for host in _subnet_unicast_targets(peer_ip):
            if host not in targets:
                targets.append(host)

    if not targets:
        return 0

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    except OSError:
        pass

    sent = 0
    for host in targets:
        try:
            sock.sendto(data, (host, port))
            sent += 1
        except OSError:
            pass
    try:
        sock.close()
    except OSError:
        pass
    return sent


def build_announce_packet(destination, app_data):
    if not destination:
        return None
    return destination.announce(app_data=app_data, send=False)


def _dest_bytes_for_hash(hash_hex):
    clean = (hash_hex or "").replace(":", "").strip().lower()
    if len(clean) != 32:
        return None
    try:
        return bytes.fromhex(clean)
    except ValueError:
        return None


def request_path_for_hash(hash_hex):
    dest_bytes = _dest_bytes_for_hash(hash_hex)
    if dest_bytes is None:
        return False
    try:
        RNS.Transport.request_path(dest_bytes)
        return True
    except Exception:
        return False


def request_paths_for_hash(hash_hex):
    """Request a path to peer on all online RNS interfaces."""
    dest_bytes = _dest_bytes_for_hash(hash_hex)
    if dest_bytes is None:
        return False
    try:
        RNS.Transport.request_path(dest_bytes)
        for iface in getattr(RNS.Transport, "interfaces", []) or []:
            if not getattr(iface, "online", True):
                continue
            try:
                RNS.Transport.request_path(dest_bytes, on_interface=iface)
            except Exception:
                pass
            spawned = getattr(iface, "spawned_interfaces", None)
            if isinstance(spawned, dict):
                for child in spawned.values():
                    if getattr(child, "online", True):
                        try:
                            RNS.Transport.request_path(dest_bytes, on_interface=child)
                        except Exception:
                            pass
        return True
    except Exception:
        return False


def udp_interface_targets():
    """Broadcast targets for patching UDPInterface forward_ip (Android fallback)."""
    targets = []
    for iface in list_network_interfaces():
        for candidate in (iface.get("subnet_broadcast"), iface.get("broadcast")):
            if candidate and candidate not in targets:
                targets.append(candidate)
    for candidate in ("255.255.255.255",):
        if candidate not in targets:
            targets.append(candidate)
    return targets