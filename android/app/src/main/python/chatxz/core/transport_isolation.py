"""Prevent RNS from bridging serial and LAN transports on dual-path nodes."""

import RNS

from chatxz.core.lan_rns import (
    interface_family,
    online_interfaces,
    serial_interface_online,
)

_LAN_FAMILIES = frozenset({"udp", "lan", "tcp"})
_SERIAL = "serial"
_patched = False
_original_path_request = None
_original_outbound = None


def dual_transport_isolation_enabled():
    """True when USB serial and at least one LAN transport are both online."""
    try:
        if serial_interface_online() is None:
            return False
        for fam in _LAN_FAMILIES:
            if online_interfaces(family=fam):
                return True
        return False
    except Exception:
        return False


def _is_lan_family(family):
    return family in _LAN_FAMILIES


def families_compatible(source_family, target_family):
    """Serial and LAN families must not forward path traffic to each other."""
    if not source_family or not target_family:
        return True
    if source_family == _SERIAL and _is_lan_family(target_family):
        return False
    if target_family == _SERIAL and _is_lan_family(source_family):
        return False
    return True


def _filter_interfaces(attached_interface, interfaces):
    if not dual_transport_isolation_enabled() or attached_interface is None:
        return list(interfaces)
    source = interface_family(attached_interface)
    if source != _SERIAL and not _is_lan_family(source):
        return list(interfaces)
    out = []
    for iface in interfaces:
        if iface == attached_interface:
            continue
        if families_compatible(source, interface_family(iface)):
            out.append(iface)
    return out


def _pin_announce_interface(packet):
    """Keep rebroadcasted announces on the interface that received them."""
    if not dual_transport_isolation_enabled():
        return
    try:
        if packet.packet_type != RNS.Packet.ANNOUNCE or packet.hops <= 0:
            return
        if packet.attached_interface is not None:
            return
        src_iface = getattr(packet, "receiving_interface", None)
        if src_iface is None:
            import RNS.Transport as Transport
            with Transport.path_table_lock:
                entry = Transport.path_table.get(packet.destination_hash)
            if entry and len(entry) > 5:
                src_iface = entry[5]
        if src_iface is None:
            import RNS.Transport as Transport
            with Transport.announce_table_lock:
                entry = Transport.announce_table.get(packet.destination_hash)
            if entry and len(entry) > 5:
                orig = entry[5]
                src_iface = getattr(orig, "receiving_interface", None)
        if src_iface is not None:
            packet.attached_interface = src_iface
    except Exception:
        pass


def apply_transport_isolation():
    """Patch RNS Transport.path_request to stay within transport zones."""
    global _patched, _original_path_request, _original_outbound
    if _patched:
        return
    try:
        import RNS.Transport as Transport
    except Exception:
        return
    _original_path_request = Transport.path_request
    _original_outbound = Transport.outbound

    @staticmethod
    def path_request(
        destination_hash,
        is_from_local_client,
        attached_interface,
        requestor_transport_id=None,
        tag=None,
    ):
        if not dual_transport_isolation_enabled() or attached_interface is None:
            return _original_path_request(
                destination_hash,
                is_from_local_client,
                attached_interface,
                requestor_transport_id=requestor_transport_id,
                tag=tag,
            )

        source = interface_family(attached_interface)
        if source == _SERIAL or _is_lan_family(source):
            try:
                with Transport.path_table_lock:
                    entry = Transport.path_table.get(destination_hash)
                if entry and len(entry) > 5:
                    path_iface = entry[5]
                    if not families_compatible(source, interface_family(path_iface)):
                        # Drop cross-zone cached path before handling the request.
                        with Transport.path_table_lock:
                            Transport.path_table.pop(destination_hash, None)
            except Exception:
                pass

        original_interfaces = Transport.interfaces
        Transport.interfaces = _filter_interfaces(attached_interface, original_interfaces)
        try:
            return _original_path_request(
                destination_hash,
                is_from_local_client,
                attached_interface,
                requestor_transport_id=requestor_transport_id,
                tag=tag,
            )
        finally:
            Transport.interfaces = original_interfaces

    @staticmethod
    def outbound(packet):
        _pin_announce_interface(packet)
        return _original_outbound(packet)

    Transport.path_request = path_request
    Transport.outbound = outbound
    _patched = True
    print("[network] Dual-transport isolation active (serial ↔ LAN path bridge blocked)")