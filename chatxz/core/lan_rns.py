"""LAN helpers for RNS UDP announces (unicast supplements broadcast on Android/Wi-Fi)."""

import socket
import time

import RNS

from chatxz.core.lan_targets import directed_broadcasts, efficient_unicast_hosts
from chatxz.utils.platform import is_android, lan_ip, list_network_interfaces

RNS_PORT = 4242


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
        for host in efficient_unicast_hosts(peer_ip=peer_ip, known_ips=known_udp_peer_ips()):
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


def interface_family(iface):
    if iface is None:
        return ""
    name = type(iface).__name__.lower()
    text = str(iface).lower()
    if "serial" in name or "tty" in text:
        return "serial"
    if "autointerfacepeer" in name or "auto" in name:
        return "lan"
    if "udp" in name:
        return "udp"
    return "other"


def interface_is_healthy(iface):
    if iface is None:
        return False
    if getattr(iface, "detached", False):
        return False
    if hasattr(iface, "online") and not iface.online:
        return False
    owner = getattr(iface, "owner", None)
    if owner is not None:
        if getattr(owner, "detached", False):
            return False
        if hasattr(owner, "online") and not owner.online:
            return False
        spawned = getattr(owner, "spawned_interfaces", None)
        addr = getattr(iface, "addr", None)
        if isinstance(spawned, dict) and addr is not None and addr not in spawned:
            return False
        ifname = getattr(iface, "ifname", None)
        if ifname:
            timed_out = getattr(owner, "timed_out_interfaces", None)
            if isinstance(timed_out, dict) and timed_out.get(ifname) is True:
                return False
    return True


def lan_mesh_has_peer():
    """True when at least one healthy AutoInterfacePeer exists (real LAN mesh path)."""
    for iface in iter_transport_interfaces():
        if type(iface).__name__ != "AutoInterfacePeer":
            continue
        if interface_is_healthy(iface):
            return True
    return False


def serial_interface_online(port=None):
    for iface in iter_transport_interfaces():
        if type(iface).__name__ != "SerialInterface":
            continue
        if port and getattr(iface, "port", None) != port:
            continue
        if interface_is_healthy(iface):
            return iface
    return None


def iter_transport_interfaces():
    for iface in getattr(RNS.Transport, "interfaces", []) or []:
        yield iface
        spawned = getattr(iface, "spawned_interfaces", None)
        if isinstance(spawned, dict):
            for child in spawned.values():
                yield child


def online_interfaces(family=None):
    out = []
    for iface in iter_transport_interfaces():
        if not interface_is_healthy(iface):
            continue
        if family and interface_family(iface) != family:
            continue
        out.append(iface)
    return out


def peer_path_entry(hash_hex):
    dest_bytes = _dest_bytes_for_hash(hash_hex)
    if dest_bytes is None:
        return None, None
    try:
        with RNS.Transport.path_table_lock:
            entry = RNS.Transport.path_table.get(dest_bytes)
        if entry and len(entry) > 5:
            return entry, entry[5]
    except Exception:
        pass
    return None, None


def peer_path_on_family(hash_hex, family):
    """Return path interface for peer when it uses the given transport family."""
    scrub_peer_path(hash_hex)
    _, path_iface = peer_path_entry(hash_hex)
    if path_iface and interface_is_healthy(path_iface):
        if family is None or interface_family(path_iface) == family:
            return path_iface
    return None


def clear_peer_path(hash_hex):
    dest_bytes = _dest_bytes_for_hash(hash_hex)
    if dest_bytes is None:
        return False
    try:
        with RNS.Transport.path_table_lock:
            if dest_bytes in RNS.Transport.path_table:
                RNS.Transport.path_table.pop(dest_bytes, None)
                return True
    except Exception:
        pass
    return False


def scrub_peer_path(hash_hex):
    """Drop cached path when it points at an offline interface."""
    _, path_iface = peer_path_entry(hash_hex)
    if path_iface and not interface_is_healthy(path_iface):
        return clear_peer_path(hash_hex)
    return False


def detach_unhealthy_interfaces():
    detached = 0
    for iface in list(iter_transport_interfaces()):
        if interface_is_healthy(iface):
            continue
        try:
            if hasattr(iface, "detach"):
                iface.detach()
                detached += 1
        except Exception:
            pass
    return detached


def request_paths_for_hash(hash_hex, family=None):
    """Request a path to peer on online RNS interfaces (optionally one family)."""
    dest_bytes = _dest_bytes_for_hash(hash_hex)
    if dest_bytes is None:
        return False
    try:
        targets = online_interfaces(family=family)
        if not targets:
            RNS.Transport.request_path(dest_bytes)
            return True
        for iface in targets:
            try:
                RNS.Transport.request_path(dest_bytes, on_interface=iface)
            except Exception:
                pass
        return True
    except Exception:
        return False


def wait_for_peer_path(hash_hex, family=None, timeout_s=12.0, poll_s=0.25):
    """Wait until path table lists peer on a healthy interface (optional family filter)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        scrub_peer_path(hash_hex)
        _, path_iface = peer_path_entry(hash_hex)
        if path_iface and interface_is_healthy(path_iface):
            fam = interface_family(path_iface)
            if family is None or fam == family:
                return path_iface
        time.sleep(poll_s)
    return None


def udp_interface_targets():
    """Broadcast targets for patching UDPInterface forward_ip (Android fallback)."""
    return directed_broadcasts()


_udp_patched = False
_known_peer_ips = set()


def register_udp_peer_ip(ip):
    """Remember a peer LAN IP for Android UDP unicast fan-out."""
    host = (ip or "").strip()
    if not host or host.startswith("127.") or host.startswith("169.254."):
        return
    _known_peer_ips.add(host)


def known_udp_peer_ips():
    return sorted(_known_peer_ips)


def register_udp_peer_ips_from_discovery(peers):
    for peer in peers or []:
        register_udp_peer_ip(peer.get("ip"))


def _udp_unicast_targets(peer_ip=None, subnet_scan=None):
    targets = []
    if peer_ip:
        register_udp_peer_ip(peer_ip)
    for host in sorted(_known_peer_ips):
        if host not in targets:
            targets.append(host)
    if subnet_scan is None:
        subnet_scan = is_android()
    if subnet_scan:
        for host in efficient_unicast_hosts(peer_ip=peer_ip, known_ips=known_udp_peer_ips()):
            if host not in targets:
                targets.append(host)
    return targets


def patch_udp_interface_unicast(force=False):
    """Prefer directed UDP to known peer IPs; Android falls back to subnet unicast."""
    global _udp_patched
    if _udp_patched and not force:
        return True
    try:
        from RNS.Interfaces.UDPInterface import UDPInterface
    except Exception:
        return False

    original = UDPInterface.process_outgoing

    def _send_directed(self, data, hosts, port):
        if not hosts:
            return False
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except OSError:
            pass
        sent_any = False
        for host in hosts:
            try:
                sock.sendto(data, (host, port))
                sent_any = True
            except OSError:
                pass
        try:
            sock.close()
        except OSError:
            pass
        if sent_any:
            self.txb += len(data)
        return sent_any

    def process_outgoing(self, data):
        port = int(getattr(self, "forward_port", None) or RNS_PORT)
        peer_targets = sorted(_known_peer_ips)

        if peer_targets and _send_directed(self, data, peer_targets, port):
            return

        if is_android():
            targets = _udp_unicast_targets(subnet_scan=True)
            if targets and _send_directed(self, data, targets, port):
                return
            forward_ip = getattr(self, "forward_ip", None)
            if forward_ip and _send_directed(self, data, [forward_ip], port):
                return
            RNS.log(
                "Could not transmit on " + str(self) + " (no UDP targets available)",
                RNS.LOG_ERROR,
            )
            return

        try:
            original(self, data)
        except Exception as exc:
            if peer_targets:
                if _send_directed(self, data, peer_targets, port):
                    return
            RNS.log(
                "Could not transmit on " + str(self)
                + ". The contained exception was: " + str(exc),
                RNS.LOG_ERROR,
            )

    UDPInterface.process_outgoing = process_outgoing
    _udp_patched = True
    label = "Android" if is_android() else "directed"
    print(f"[network] {label} UDP transmit patch installed (RNS port {RNS_PORT})")
    return True
