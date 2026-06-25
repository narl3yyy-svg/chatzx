"""RNS interface preset management for chatxz config generation."""

import copy
import glob
import os
import sys
import time
import uuid

INTERFACE_PRESETS = {
    "udp_lan": {
        "label": "UDP LAN",
        "type": "UDPInterface",
        "defaults": {
            "enabled": True,
            "listen_ip": "0.0.0.0",
            "listen_port": 4242,
            "forward_ip": "255.255.255.255",
            "forward_port": 4242,
            "ifac_size": 16,
        },
    },
    "tcp_lan": {
        "label": "TCP LAN",
        "type": "TCPServerInterface",
        "defaults": {
            "enabled": True,
            "listen_ip": "0.0.0.0",
            "listen_port": 4242,
            "ifac_size": 16,
        },
    },
    "tcp_client": {
        "label": "TCP Client",
        "type": "TCPClientInterface",
        "defaults": {
            "enabled": True,
            "target_host": "127.0.0.1",
            "target_port": 4242,
            "ifac_size": 16,
        },
    },
    "tcp_server": {
        "label": "TCP Hub Server",
        "type": "TCPServerInterface",
        "defaults": {
            "enabled": True,
            "listen_ip": "0.0.0.0",
            "listen_port": 4242,
            "ifac_size": 16,
        },
    },
    "serial": {
        "label": "Serial",
        "type": "SerialInterface",
        "defaults": {
            "enabled": False,
            "port": "",
            "speed": 57600,
            "ifac_size": 16,
        },
    },
}

SERIAL_DEFAULT_BAUD = 57600
_serial_hot_add_callback = None
_last_serial_unavail_log = 0.0

SERIAL_BAUD_RATES = [
    1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600,
]

SERIAL_ACCESS_GROUPS = ("dialout", "uucp")

SERIAL_PERMISSION_HINT = (
    "Serial port access denied for this chatxz process. "
    "Ubuntu: sudo usermod -aG dialout $USER — then fully log out of Ubuntu and log back in "
    "(a new terminal is not enough). Stop chatxz, start it again, then refresh serial ports."
)

ANDROID_SERIAL_PERMISSION_HINT = (
    "USB serial permission required. Plug in your USB adapter (OTG cable), tap Refresh devices, "
    "then tap Grant USB access when prompted. Restart the app after applying serial settings."
)


def serial_permission_hint_for_process():
    try:
        from chatxz.utils.platform import is_android
        if is_android():
            return ANDROID_SERIAL_PERMISSION_HINT
    except Exception:
        pass
    if user_has_serial_group_access():
        return (
            "Port exists but this chatxz process still cannot open it. "
            "Stop chatxz completely and start it again after logging out/in."
        )
    return SERIAL_PERMISSION_HINT

DEFAULT_INTERFACE_LIST = [
    {
        "id": "tcp-client",
        "preset": "tcp_client",
        "name": "TCP Client",
        "type": "TCPClientInterface",
        "enabled": True,
        "target_host": "127.0.0.1",
        "target_port": 4242,
        "ifac_size": 16,
    },
]

ANDROID_DEFAULT_INTERFACE_LIST = [
    {
        "id": "udp-lan",
        "preset": "udp_lan",
        "name": "UDP Interface",
        "type": "UDPInterface",
        "enabled": True,
        "listen_ip": "0.0.0.0",
        "listen_port": 4242,
        "forward_ip": "255.255.255.255",
        "forward_port": 4242,
        "ifac_size": 16,
    },
]


def standalone_needs_udp(interfaces, hub_role="off"):
    """True when only a loopback TCP client is configured with no hub — LAN cannot work."""
    return android_standalone_needs_udp(interfaces, hub_role)


def default_interface_list():
    """Fresh installs get UDP LAN so discovery works without manual TCP hub setup."""
    try:
        from chatxz.utils.platform import is_android
        if is_android():
            return copy.deepcopy(ANDROID_DEFAULT_INTERFACE_LIST)
    except Exception:
        pass
    return copy.deepcopy(ANDROID_DEFAULT_INTERFACE_LIST)


def android_standalone_needs_udp(interfaces, hub_role="off"):
    """True when Android has only a loopback TCP client and no hub — cannot work standalone."""
    if hub_role and hub_role != "off":
        return False
    items = normalize_interface_list(interfaces)
    if not items:
        return True
    has_udp = any(i.get("type") == "UDPInterface" for i in items)
    if has_udp:
        return False
    has_tcp_lan = any(i.get("preset") == "tcp_lan" for i in items)
    if has_tcp_lan:
        return False
    if len(items) != 1:
        return False
    only = items[0]
    if only.get("type") != "TCPClientInterface":
        return False
    host = (only.get("target_host") or "").strip().lower()
    return host in ("127.0.0.1", "localhost", "")


def _new_id():
    return uuid.uuid4().hex[:8]


def load_settings_interfaces(config_dir=None):
    """Load rns_interfaces from settings.json (best-effort)."""
    try:
        from chatxz.utils.helpers import get_config_dir
        import json

        root = config_dir or get_config_dir()
        path = os.path.join(root, "settings.json")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("rns_interfaces")
    except Exception:
        return None


def configured_udp_lan_enabled(interfaces=None, config_dir=None):
    """True when UDP LAN preset is present and enabled in settings."""
    items = normalize_interface_list(interfaces or load_settings_interfaces(config_dir))
    return any(
        i.get("type") == "UDPInterface" and i.get("enabled", True)
        for i in items
    )


def configured_tcp_lan_enabled(interfaces=None, config_dir=None):
    """True when TCP LAN preset is present and enabled in settings."""
    items = normalize_interface_list(interfaces or load_settings_interfaces(config_dir))
    return any(
        i.get("preset") == "tcp_lan" and i.get("enabled", True)
        for i in items
    )


def configured_tcp_lan_listen(interfaces=None, config_dir=None):
    """Return (listen_ip, listen_port, ifac_size) for the enabled TCP LAN preset."""
    listen_ip = "0.0.0.0"
    listen_port = 4242
    ifac_size = 16
    for iface in normalize_interface_list(interfaces or load_settings_interfaces(config_dir)):
        if iface.get("preset") != "tcp_lan" or not iface.get("enabled", True):
            continue
        listen_ip = (iface.get("listen_ip") or listen_ip).strip() or "0.0.0.0"
        listen_port = int(iface.get("listen_port") or listen_port)
        ifac_size = int(iface.get("ifac_size") or ifac_size)
        break
    return listen_ip, listen_port, ifac_size


def configured_serial_enabled(interfaces=None, config_dir=None):
    """True when a serial port is configured and accessible."""
    items = normalize_interface_list(interfaces or load_settings_interfaces(config_dir))
    for iface in items:
        if iface.get("preset") != "serial" and iface.get("type") != "SerialInterface":
            continue
        if serial_runtime_active(iface):
            return True
    return False


def lan_discovery_configured(interfaces=None, config_dir=None):
    """True when LAN discovery/beacon should be active (UDP LAN or TCP LAN)."""
    return (
        configured_udp_lan_enabled(interfaces, config_dir)
        or configured_tcp_lan_enabled(interfaces, config_dir)
    )


def user_has_serial_group_access():
    """True if the current user belongs to a group that can access serial ports."""
    try:
        import grp
        groups = set(os.getgroups())
        for name in SERIAL_ACCESS_GROUPS:
            try:
                if grp.getgrnam(name).gr_gid in groups:
                    return True
            except KeyError:
                continue
    except Exception:
        pass
    return False


def _android_serial_port_status(port):
    path = (port or "").strip()
    if not path:
        return "none"
    try:
        from usb4a import usb
        device = usb.get_usb_device(path)
        if not device:
            return "missing"
        if usb.has_usb_permission(device):
            return "ok"
        return "permission_denied"
    except Exception:
        return "missing"


def serial_port_status(port):
    """Return none, missing, permission_denied, or ok."""
    path = (port or "").strip()
    if not path:
        return "none"
    try:
        from chatxz.utils.platform import is_android
        if is_android():
            return _android_serial_port_status(path)
    except Exception:
        pass
    if not os.path.exists(path):
        return "missing"
    if not os.access(path, os.R_OK | os.W_OK):
        return "permission_denied"
    return "ok"


def serial_port_accessible(port):
    return serial_port_status(port) == "ok"


def serial_port_available(port):
    """Backward-compatible alias for serial_port_accessible."""
    return serial_port_accessible(port)


def serial_runtime_active(iface):
    """True when a serial port is configured and this process can open it."""
    if iface.get("preset") != "serial" and iface.get("type") != "SerialInterface":
        return False
    port = (iface.get("port") or "").strip()
    if not port:
        return False
    return serial_port_accessible(port)


def _sync_serial_enabled(iface):
    """Keep enabled in sync with port selection and live accessibility."""
    if iface.get("preset") != "serial" and iface.get("type") != "SerialInterface":
        return iface
    if iface.get("user_disabled"):
        iface["enabled"] = False
        return iface
    port = (iface.get("port") or "").strip()
    if not port:
        iface["enabled"] = False
    else:
        iface["enabled"] = serial_port_status(port) == "ok"
    return iface


def serial_skip_reason(port):
    status = serial_port_status(port)
    path = (port or "").strip() or "(none)"
    if status == "permission_denied":
        return path, "permission denied — " + SERIAL_PERMISSION_HINT
    if status == "missing":
        return path, "not connected"
    if status == "none":
        return path, "no port selected"
    return path, "inactive"


def normalize_interface_list(items):
    if not items:
        return default_interface_list()
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        preset = item.get("preset") or "udp_lan"
        base = copy.deepcopy(INTERFACE_PRESETS.get(preset, INTERFACE_PRESETS["udp_lan"])["defaults"])
        merged = {**base, **item}
        merged.setdefault("id", _new_id())
        merged.setdefault("preset", preset)
        merged.setdefault("name", INTERFACE_PRESETS.get(preset, {}).get("label", merged.get("type", "Interface")))
        merged["type"] = INTERFACE_PRESETS.get(preset, {}).get("type", merged.get("type", "UDPInterface"))
        out.append(_sync_serial_enabled(merged))
    if not out:
        return default_interface_list()
    seen_serial_ports = set()
    seen_udp = False
    seen_tcp_lan = False
    deduped = []
    for item in out:
        itype = item.get("type")
        preset = item.get("preset")
        if itype == "SerialInterface":
            port = (item.get("port") or "").strip()
            if port and port in seen_serial_ports:
                continue
            if port:
                seen_serial_ports.add(port)
        elif itype == "UDPInterface" or preset == "udp_lan":
            if seen_udp:
                continue
            seen_udp = True
        elif preset == "tcp_lan" or (
            itype == "TCPServerInterface" and preset not in ("tcp_server",)
        ):
            if seen_tcp_lan:
                continue
            seen_tcp_lan = True
        deduped.append(item)
    return deduped


def _pick_default_serial_port():
    ports = list_serial_ports()
    if not ports:
        return ""
    for port in ports:
        if port.get("accessible"):
            return port.get("device") or ""
    return ports[0].get("device") or ""


def add_interface(items, preset_key):
    preset = INTERFACE_PRESETS.get(preset_key)
    if not preset:
        raise ValueError(f"Unknown preset: {preset_key}")
    items = normalize_interface_list(items)
    if preset_key == "udp_lan":
        if any(
            i.get("preset") == "udp_lan" or i.get("type") == "UDPInterface"
            for i in items
        ):
            return items
    if preset_key == "tcp_lan":
        if any(i.get("preset") == "tcp_lan" for i in items):
            return items
    entry = {
        "id": _new_id(),
        "preset": preset_key,
        "name": f"{preset['label']} {_new_id()}",
        **copy.deepcopy(preset["defaults"]),
    }
    if preset_key == "serial":
        entry["port"] = _pick_default_serial_port()
        entry["speed"] = SERIAL_DEFAULT_BAUD
        entry = _sync_serial_enabled(entry)
    items.append(entry)
    return items


def set_primary_lan_transport(interfaces, preset_key):
    """Replace UDP/TCP LAN presets with a single chosen LAN transport."""
    if preset_key not in ("udp_lan", "tcp_lan"):
        return normalize_interface_list(interfaces)
    items = normalize_interface_list(interfaces)
    kept = []
    for iface in items:
        preset = iface.get("preset")
        itype = iface.get("type")
        if preset in ("udp_lan", "tcp_lan"):
            continue
        if itype == "UDPInterface":
            continue
        if preset == "tcp_server" or (itype == "TCPServerInterface" and preset == "tcp_server"):
            kept.append(iface)
            continue
        if itype == "TCPServerInterface" and preset != "tcp_server":
            continue
        kept.append(iface)
    if any(i.get("preset") == preset_key for i in kept):
        return kept
    return add_interface(kept, preset_key)


def delete_interface(items, iface_id):
    items = normalize_interface_list(items)
    return [i for i in items if i.get("id") != iface_id]


def _is_useful_serial_port(entry):
    device = entry.device or ""
    if any(
        device.startswith(prefix)
        for prefix in ("/dev/ttyUSB", "/dev/ttyACM", "/dev/ttyAMA", "/dev/rfcomm", "/dev/cu.")
    ):
        return True
    desc = (entry.description or "").strip().lower()
    hwid = (entry.hwid or "").strip().lower()
    if desc and desc not in ("n/a", "none"):
        return True
    if hwid and hwid not in ("n/a", "none"):
        return True
    if "/ttyS" in device:
        return False
    return bool(device)


def _glob_serial_devices():
    devices = set()
    for pattern in ("/dev/ttyUSB*", "/dev/ttyACM*", "/dev/ttyAMA*", "/dev/rfcomm*"):
        devices.update(glob.glob(pattern))
    return sorted(devices)


def _serial_port_entry(device, description="", hwid=""):
    status = serial_port_status(device)
    return {
        "device": device,
        "description": description or "",
        "hwid": hwid or "",
        "accessible": status == "ok",
        "status": status,
    }


def list_android_usb_serial_ports():
    """Return USB serial devices visible to the Android USB host API."""
    try:
        from usb4a import usb
    except Exception as exc:
        print(f"[serial] Android USB modules unavailable: {exc}")
        return []
    by_device = {}
    try:
        for device in usb.get_usb_device_list():
            if device is None:
                continue
            name = str(device.getDeviceName())
            vid = int(device.getVendorId())
            pid = int(device.getProductId())
            mfr = device.getManufacturerName()
            prod = device.getProductName()
            desc_parts = [p for p in (mfr, prod) if p]
            description = " ".join(desc_parts).strip() or f"USB serial {vid:04x}:{pid:04x}"
            by_device[name] = _serial_port_entry(
                name,
                description,
                f"VID:PID={vid:04x}:{pid:04x}",
            )
    except Exception as exc:
        print(f"[serial] Android USB enumeration failed: {exc}")
    return [by_device[k] for k in sorted(by_device)]


def list_serial_ports():
    """Return serial devices from pyserial and /dev/ttyUSB* /dev/ttyACM* globs."""
    try:
        from chatxz.utils.platform import is_android
        if is_android():
            return list_android_usb_serial_ports()
    except Exception:
        pass
    by_device = {}
    try:
        from serial.tools import list_ports
        for entry in sorted(list_ports.comports(), key=lambda p: p.device):
            if not _is_useful_serial_port(entry):
                continue
            by_device[entry.device] = _serial_port_entry(
                entry.device, entry.description or "", entry.hwid or ""
            )
    except Exception:
        pass
    for device in _glob_serial_devices():
        if device not in by_device:
            by_device[device] = _serial_port_entry(device)
    return [by_device[k] for k in sorted(by_device)]


def tcp_client_target_is_local(target_host):
    host = (target_host or "").strip().lower()
    if not host or host in ("127.0.0.1", "localhost", "0.0.0.0"):
        return True
    try:
        from chatxz.utils.platform import local_ipv4_addresses
        return host in {ip.lower() for ip in local_ipv4_addresses()}
    except Exception:
        return False


def tcp_client_target_warning(target_host):
    if not tcp_client_target_is_local(target_host):
        return None
    return (
        "TCP Client target is this machine. For LAN peers, use TCP Hub Server here "
        "and TCP Client on the remote device (or use UDP LAN / TCP LAN for subnet peers)."
    )


def update_interface(items, iface_id, updates):
    items = normalize_interface_list(items)
    if not iface_id:
        raise ValueError("id required")
    found = False
    out = []
    for item in items:
        if item.get("id") != iface_id:
            out.append(item)
            continue
        found = True
        updated = {**item}
        preset = updated.get("preset") or ""
        itype = updated.get("type", "")
        if "enabled" in updates:
            updated["enabled"] = bool(updates["enabled"])
            if preset == "serial" or itype == "SerialInterface":
                updated["user_disabled"] = not bool(updates["enabled"])
        if preset == "serial" or itype == "SerialInterface":
            if "port" in updates:
                updated["port"] = str(updates["port"] or "").strip()
            if "speed" in updates and updates["speed"] is not None:
                updated["speed"] = int(updates["speed"])
            updated = _sync_serial_enabled(updated)
        elif preset in ("tcp_client", "tcp_server", "tcp_lan") or itype in ("TCPClientInterface", "TCPServerInterface"):
            if "target_host" in updates and updates["target_host"]:
                updated["target_host"] = str(updates["target_host"]).strip()
            if "target_port" in updates and updates["target_port"] is not None:
                updated["target_port"] = int(updates["target_port"])
            if "listen_ip" in updates and updates["listen_ip"]:
                updated["listen_ip"] = str(updates["listen_ip"]).strip()
            if "listen_port" in updates and updates["listen_port"] is not None:
                updated["listen_port"] = int(updates["listen_port"])
        elif preset == "udp_lan" or itype == "UDPInterface":
            for key in ("listen_ip", "listen_port", "forward_ip", "forward_port"):
                if key in updates and updates[key] is not None:
                    updated[key] = updates[key]
        out.append(updated)
    if not found:
        raise ValueError(f"Interface not found: {iface_id}")
    return out


def configured_serial_port(settings_interfaces=None):
    for iface in normalize_interface_list(settings_interfaces):
        if iface.get("type") != "SerialInterface":
            continue
        port = (iface.get("port") or "").strip()
        if port:
            return port, int(iface.get("speed") or SERIAL_DEFAULT_BAUD)
    return "", SERIAL_DEFAULT_BAUD


def _stop_serial_reconnect(iface):
    iface.online = False
    try:
        iface.reconnect_port = lambda: None
    except Exception:
        pass
    serial = getattr(iface, "serial", None)
    if serial is not None:
        try:
            serial.close()
        except Exception:
            pass
        try:
            iface.serial = None
        except Exception:
            pass


def _finalize_rns_interface(iface, ifac_size=16):
    """Apply the same post-init fields Reticulum sets when loading config."""
    import RNS
    from RNS.Interfaces.Interface import Interface

    iface.mode = Interface.MODE_FULL
    iface.OUT = True
    iface.IN = True
    iface.ifac_size = ifac_size
    iface.announce_cap = RNS.Reticulum.ANNOUNCE_CAP / 100.0
    iface.announce_rate_target = None
    iface.announce_rate_grace = None
    iface.announce_rate_penalty = None
    if hasattr(iface, "optimise_mtu"):
        iface.optimise_mtu()
    if hasattr(iface, "final_init"):
        iface.final_init()


def dedupe_serial_interfaces(port=None):
    """Keep one SerialInterface per USB port — duplicates break the link."""
    try:
        import RNS
    except Exception:
        return 0
    keepers = {}
    removed = 0
    for iface in list(getattr(RNS.Transport, "interfaces", []) or []):
        if type(iface).__name__ != "SerialInterface":
            continue
        p = getattr(iface, "port", None)
        if port and p != port:
            continue
        if not p:
            continue
        prev = keepers.get(p)
        if prev is None:
            keepers[p] = iface
            continue
        drop = iface
        if getattr(prev, "online", False) and not getattr(iface, "online", False):
            drop = iface
        elif getattr(iface, "online", False) and not getattr(prev, "online", False):
            keepers[p] = iface
            drop = prev
        _stop_serial_reconnect(drop)
        try:
            RNS.Transport.remove_interface(drop)
            removed += 1
            print(f"[serial] Removed duplicate SerialInterface on {p}")
        except Exception:
            pass
        if drop is prev:
            keepers[p] = iface
    return removed


def remove_serial_interfaces(port=None):
    """Remove SerialInterface(s) from the running transport (stops reconnect spam)."""
    try:
        import RNS
    except Exception:
        return 0
    removed = 0
    for iface in list(getattr(RNS.Transport, "interfaces", []) or []):
        if type(iface).__name__ != "SerialInterface":
            continue
        if port and getattr(iface, "port", None) != port:
            continue
        _stop_serial_reconnect(iface)
        try:
            RNS.Transport.remove_interface(iface)
            removed += 1
            print(f"[serial] Removed RNS SerialInterface {getattr(iface, 'name', iface)}")
        except Exception as exc:
            print(f"[serial] Could not remove SerialInterface: {exc}")
    return removed


def prune_dead_serial_interfaces():
    """Drop broken or offline serial interfaces so announces/paths use LAN only."""
    try:
        import RNS
    except Exception:
        return 0
    removed = 0
    for iface in list(getattr(RNS.Transport, "interfaces", []) or []):
        if type(iface).__name__ != "SerialInterface":
            continue
        port = getattr(iface, "port", None)
        broken = not hasattr(iface, "mode")
        unplugged = bool(port) and not serial_port_accessible(port)
        if broken or unplugged:
            _stop_serial_reconnect(iface)
            try:
                RNS.Transport.remove_interface(iface)
                removed += 1
            except Exception:
                pass
    if removed:
        print(f"[serial] Pruned {removed} dead SerialInterface(s)")
    return removed


def register_serial_hot_add_callback(callback):
    """Register callback invoked when a new SerialInterface is hot-added at runtime."""
    global _serial_hot_add_callback
    _serial_hot_add_callback = callback


def _notify_serial_hot_add(iface):
    cb = _serial_hot_add_callback
    if not cb or not iface:
        return
    try:
        cb(iface)
    except Exception as exc:
        print(f"[serial] Hot-add callback error: {exc}")


def hot_add_serial_interface(port, speed=SERIAL_DEFAULT_BAUD, ifac_size=16):
    """Attach SerialInterface to a running RNS instance when USB is plugged in later."""
    port = (port or "").strip()
    if not port or not serial_port_accessible(port):
        return None
    try:
        from chatxz.utils.platform import is_android
        if is_android():
            from chatxz.core.android_serial import ensure_android_serial_patch
            ensure_android_serial_patch()
    except Exception:
        pass
    try:
        import RNS
        from RNS.Interfaces.SerialInterface import SerialInterface
    except Exception as exc:
        print(f"[serial] Hot-add unavailable: {exc}")
        return None

    dedupe_serial_interfaces(port)
    for iface in getattr(RNS.Transport, "interfaces", []) or []:
        if type(iface).__name__ != "SerialInterface":
            continue
        if getattr(iface, "port", None) != port:
            continue
        if getattr(iface, "online", False) and hasattr(iface, "mode"):
            return iface
        remove_serial_interfaces(port)
        break

    name = f"Serial {port}"
    try:
        iface = SerialInterface(RNS.Transport, {
            "name": name,
            "port": port,
            "speed": int(speed),
            "ifac_size": ifac_size,
        })
        _finalize_rns_interface(iface, ifac_size=ifac_size)
        RNS.Transport.add_interface(iface)
        dedupe_serial_interfaces(port)
        print(f"[serial] Hot-added RNS SerialInterface on {port}")
        _notify_serial_hot_add(iface)
        return iface
    except Exception as exc:
        print(f"[serial] Hot-add failed for {port}: {exc}")
        return None


def tcp_server_interface_online(listen_port=None):
    """Return an online TCPServerInterface, optionally matching listen_port."""
    try:
        import RNS
        for iface in getattr(RNS.Transport, "interfaces", []) or []:
            if type(iface).__name__ != "TCPServerInterface":
                continue
            if listen_port is not None:
                port = getattr(iface, "listen_port", None) or getattr(iface, "port", None)
                if port is not None and int(port) != int(listen_port):
                    continue
            if getattr(iface, "online", False):
                return iface
    except Exception:
        pass
    return None


def tcp_client_interface_online():
    """Return an online TCPClientInterface if any."""
    try:
        import RNS
        for iface in getattr(RNS.Transport, "interfaces", []) or []:
            if type(iface).__name__ == "TCPClientInterface" and getattr(iface, "online", False):
                return iface
    except Exception:
        pass
    return None


def hot_add_tcp_server_interface(
    listen_ip="0.0.0.0", listen_port=4242, ifac_size=16, name=None, log_tag="hub",
):
    """Attach TCPServerInterface when hub server or TCP LAN is enabled after RNS started."""
    listen_ip = (listen_ip or "0.0.0.0").strip()
    listen_port = int(listen_port or 4242)
    try:
        import RNS
        from RNS.Interfaces.TCPInterface import TCPServerInterface
    except Exception as exc:
        print(f"[{log_tag}] TCP server hot-add unavailable: {exc}")
        return None

    existing = tcp_server_interface_online(listen_port)
    if existing:
        return existing

    for iface in list(getattr(RNS.Transport, "interfaces", []) or []):
        if type(iface).__name__ != "TCPServerInterface":
            continue
        try:
            RNS.Transport.remove_interface(iface)
        except Exception:
            pass

    iface_name = name or f"TCP Hub {listen_port}"
    try:
        iface = TCPServerInterface(RNS.Transport, {
            "name": iface_name,
            "listen_ip": listen_ip,
            "listen_port": listen_port,
            "ifac_size": ifac_size,
        })
        _finalize_rns_interface(iface, ifac_size=ifac_size)
        RNS.Transport.add_interface(iface)
        print(f"[{log_tag}] Hot-added TCP server on {listen_ip}:{listen_port}")
        return iface
    except Exception as exc:
        print(f"[{log_tag}] TCP server hot-add failed for {listen_ip}:{listen_port}: {exc}")
        return None


def remove_tcp_client_interfaces():
    """Remove TCPClientInterface(s) from the running transport."""
    try:
        import RNS
    except Exception:
        return 0
    removed = 0
    for iface in list(getattr(RNS.Transport, "interfaces", []) or []):
        if type(iface).__name__ != "TCPClientInterface":
            continue
        try:
            RNS.Transport.remove_interface(iface)
            removed += 1
            print(f"[hub] Removed RNS TCPClientInterface {getattr(iface, 'name', iface)}")
        except Exception as exc:
            print(f"[hub] Could not remove TCPClientInterface: {exc}")
    return removed


def hot_add_tcp_client_interface(target_host, target_port=4242, ifac_size=16, log_tag="hub"):
    """Attach TCPClientInterface for hub client or TCP LAN peer dial."""
    target_host = (target_host or "").strip()
    target_port = int(target_port or 4242)
    if not target_host:
        return None
    try:
        import RNS
        from RNS.Interfaces.TCPInterface import TCPClientInterface
    except Exception as exc:
        print(f"[{log_tag}] TCP client hot-add unavailable: {exc}")
        return None

    for iface in list(getattr(RNS.Transport, "interfaces", []) or []):
        if type(iface).__name__ != "TCPClientInterface":
            continue
        host = (getattr(iface, "target_host", None) or "").strip()
        port = int(
            getattr(iface, "target_port", None)
            or getattr(iface, "port", None)
            or 4242
        )
        if (
            host == target_host
            and port == target_port
            and getattr(iface, "online", False)
        ):
            return iface
        try:
            RNS.Transport.remove_interface(iface)
        except Exception:
            pass

    name = f"TCP Client {target_host}:{target_port}"
    try:
        iface = TCPClientInterface(RNS.Transport, {
            "name": name,
            "target_host": target_host,
            "target_port": target_port,
            "ifac_size": ifac_size,
        })
        _finalize_rns_interface(iface, ifac_size=ifac_size)
        RNS.Transport.add_interface(iface)
        print(f"[{log_tag}] Hot-added TCP client to {target_host}:{target_port}")
        return iface
    except Exception as exc:
        print(f"[{log_tag}] TCP client hot-add failed for {target_host}:{target_port}: {exc}")
        return None


def ensure_runtime_tcp_client(settings=None, config_dir=None):
    """Dial TCP hub server when hub_role is client (runtime hot-add)."""
    if not settings:
        try:
            from chatxz.utils.helpers import get_config_dir
            import json
            path = os.path.join(config_dir or get_config_dir(), "settings.json")
            with open(path, encoding="utf-8") as fh:
                settings = json.load(fh)
        except Exception:
            return None
    if (settings.get("hub_role") or "off") != "client":
        return None
    host = (settings.get("hub_host") or "").strip()
    if not host:
        return None
    try:
        import RNS
        if RNS.Reticulum.get_instance() is None:
            return None
    except Exception:
        return None
    port = int(settings.get("hub_port") or 4242)
    ifac_size = 16
    for iface in normalize_interface_list(settings.get("rns_interfaces")):
        if iface.get("type") != "TCPClientInterface":
            continue
        if not iface.get("enabled", True):
            continue
        ifac_size = int(iface.get("ifac_size") or ifac_size)
        break
    return hot_add_tcp_client_interface(
        target_host=host, target_port=port, ifac_size=ifac_size,
    )


def ensure_runtime_tcp_lan_server(settings=None, config_dir=None):
    """Start TCP LAN listener when tcp_lan preset is enabled (not hub mode)."""
    if not settings:
        try:
            from chatxz.utils.helpers import get_config_dir
            import json
            path = os.path.join(config_dir or get_config_dir(), "settings.json")
            with open(path, encoding="utf-8") as fh:
                settings = json.load(fh)
        except Exception:
            return None
    if (settings.get("hub_role") or "off") != "off":
        return None
    if not configured_tcp_lan_enabled(settings.get("rns_interfaces")):
        return None
    try:
        import RNS
        if RNS.Reticulum.get_instance() is None:
            return None
    except Exception:
        return None
    listen_ip, listen_port, ifac_size = configured_tcp_lan_listen(
        settings.get("rns_interfaces"), config_dir,
    )
    return hot_add_tcp_server_interface(
        listen_ip=listen_ip,
        listen_port=listen_port,
        ifac_size=ifac_size,
        name=f"TCP LAN {listen_port}",
        log_tag="tcp-lan",
    )


def ensure_tcp_client_to_peer(peer_ip, port=None, settings=None, config_dir=None):
    """Dial a discovered peer over TCP LAN (runtime hot-add)."""
    peer_ip = (peer_ip or "").strip()
    if not peer_ip:
        return None
    if not settings:
        try:
            from chatxz.utils.helpers import get_config_dir
            import json
            path = os.path.join(config_dir or get_config_dir(), "settings.json")
            with open(path, encoding="utf-8") as fh:
                settings = json.load(fh)
        except Exception:
            return None
    if (settings.get("hub_role") or "off") == "client":
        return None
    if not configured_tcp_lan_enabled(settings.get("rns_interfaces")):
        return None
    try:
        import RNS
        if RNS.Reticulum.get_instance() is None:
            return None
    except Exception:
        return None
    if port is None:
        _, port, ifac_size = configured_tcp_lan_listen(
            settings.get("rns_interfaces"), config_dir,
        )
    else:
        _, _, ifac_size = configured_tcp_lan_listen(
            settings.get("rns_interfaces"), config_dir,
        )
    return hot_add_tcp_client_interface(
        target_host=peer_ip,
        target_port=int(port or 4242),
        ifac_size=ifac_size,
        log_tag="tcp-lan",
    )


def ensure_runtime_tcp_hub(settings=None, config_dir=None):
    """Start TCP hub listener when hub_role is server (runtime hot-add)."""
    if not settings:
        try:
            from chatxz.utils.helpers import get_config_dir
            import json
            path = os.path.join(config_dir or get_config_dir(), "settings.json")
            with open(path, encoding="utf-8") as fh:
                settings = json.load(fh)
        except Exception:
            return None
    if (settings.get("hub_role") or "off") != "server":
        return None
    try:
        import RNS
        if RNS.Reticulum.get_instance() is None:
            return None
    except Exception:
        return None
    listen_ip = "0.0.0.0"
    listen_port = int(settings.get("hub_port") or 4242)
    ifac_size = 16
    for iface in normalize_interface_list(settings.get("rns_interfaces")):
        if iface.get("type") != "TCPServerInterface":
            continue
        if not iface.get("enabled", True):
            continue
        listen_ip = (iface.get("listen_ip") or listen_ip).strip() or "0.0.0.0"
        listen_port = int(iface.get("listen_port") or listen_port)
        ifac_size = int(iface.get("ifac_size") or ifac_size)
        break
    return hot_add_tcp_server_interface(
        listen_ip=listen_ip, listen_port=listen_port, ifac_size=ifac_size,
    )


def ensure_runtime_serial(settings_interfaces=None):
    port, speed = configured_serial_port(settings_interfaces)
    if not port:
        return None
    existing = None
    try:
        import RNS
        for iface in getattr(RNS.Transport, "interfaces", []) or []:
            if type(iface).__name__ == "SerialInterface" and getattr(iface, "port", None) == port:
                existing = iface
                break
    except Exception:
        existing = None
    if existing:
        if getattr(existing, "online", False) and hasattr(existing, "mode"):
            return existing
        if not serial_port_accessible(port):
            remove_serial_interfaces(port)
            return None
        remove_serial_interfaces(port)
    if serial_port_accessible(port):
        added = hot_add_serial_interface(port, speed=speed)
        if added:
            return added
        print(f"[serial] Hot-add skipped for {port} — interface already loaded or port busy")
        try:
            import RNS
            for iface in getattr(RNS.Transport, "interfaces", []) or []:
                if type(iface).__name__ == "SerialInterface" and getattr(iface, "port", None) == port:
                    if getattr(iface, "online", False):
                        return iface
        except Exception:
            pass
        return None
    global _last_serial_unavail_log
    now = time.time()
    if now - _last_serial_unavail_log >= 30.0:
        status = serial_port_status(port)
        print(f"[serial] Runtime serial unavailable on {port} ({status})")
        _last_serial_unavail_log = now
    return None


def render_rns_config(
    interfaces, broadcast_ip=None, android=False, log=print, auto_interface_enabled=True,
):
    normalized = normalize_interface_list(interfaces)
    has_tcp_server = any(
        i.get("type") == "TCPServerInterface" and i.get("enabled", True)
        for i in normalized
    )
    has_serial = any(
        i.get("type") == "SerialInterface" and i.get("enabled", True)
        for i in normalized
    )
    has_udp = any(
        i.get("type") == "UDPInterface" and i.get("enabled", True)
        for i in normalized
    )
    has_tcp_lan = configured_tcp_lan_enabled(normalized)
    # Android needs transport enabled for serial/UDP/TCP LAN path discovery.
    enable_transport = "Yes" if (
        not android or has_tcp_server or has_serial or has_udp or has_tcp_lan
    ) else "No"
    lines = [
        "[reticulum]",
        f"enable_transport = {enable_transport}",
        "share_instance = No",
        "",
        "[logging]",
        "loglevel = 3" if not android else "loglevel = 4",
        "",
        "[interfaces]",
    ]
    skipped_serial = []
    seen_serial_ports = set()
    seen_udp = False
    seen_tcp_lan = False
    for iface in normalized:
        itype = iface.get("type", "")
        if itype == "SerialInterface":
            port = (iface.get("port") or "").strip()
            if port:
                if port in seen_serial_ports:
                    continue
                seen_serial_ports.add(port)
            if android:
                port, reason = serial_skip_reason(iface.get("port"))
                skipped_serial.append((iface.get("name") or "Serial", port, "hot-add on Android"))
                continue
            if serial_runtime_active(iface):
                name = iface.get("name") or iface.get("type", "Serial")
                lines.append(f"  [[{name}]]")
                lines.append("    type = SerialInterface")
                lines.append("    enabled = Yes")
                lines.append(f"    port = {iface.get('port', '/dev/ttyUSB0')}")
                lines.append(f"    speed = {iface.get('speed', SERIAL_DEFAULT_BAUD)}")
                if iface.get("ifac_size"):
                    lines.append(f"    ifac_size = {iface.get('ifac_size')}")
                lines.append("")
                continue
            port, reason = serial_skip_reason(iface.get("port"))
            skipped_serial.append((iface.get("name") or "Serial", port, reason))
            continue
        elif not iface.get("enabled", True):
            continue
        preset = iface.get("preset")
        if itype == "UDPInterface" or preset == "udp_lan":
            if seen_udp:
                continue
            seen_udp = True
        elif preset == "tcp_lan" or (
            itype == "TCPServerInterface" and preset not in ("tcp_server",)
        ):
            if seen_tcp_lan:
                continue
            seen_tcp_lan = True
        name = iface.get("name") or iface.get("type", "Interface")
        lines.append(f"  [[{name}]]")
        lines.append(f"    type = {iface.get('type', 'UDPInterface')}")
        lines.append("    enabled = Yes")
        if itype == "UDPInterface":
            listen_ip = iface.get("listen_ip", "0.0.0.0")
            forward_ip = iface.get("forward_ip") or broadcast_ip or "255.255.255.255"
            lines.append(f"    listen_ip = {listen_ip}")
            lines.append(f"    listen_port = {iface.get('listen_port', 4242)}")
            lines.append(f"    forward_ip = {forward_ip}")
            lines.append(f"    forward_port = {iface.get('forward_port', 4242)}")
            if iface.get("ifac_size"):
                lines.append(f"    ifac_size = {iface.get('ifac_size')}")
        elif itype in ("TCPClientInterface", "TCPServerInterface"):
            if itype == "TCPServerInterface":
                lines.append(f"    listen_ip = {iface.get('listen_ip', '0.0.0.0')}")
                lines.append(f"    listen_port = {iface.get('listen_port', 4242)}")
            else:
                lines.append(f"    target_host = {iface.get('target_host', '127.0.0.1')}")
                lines.append(f"    target_port = {iface.get('target_port', 4242)}")
            if iface.get("ifac_size"):
                lines.append(f"    ifac_size = {iface.get('ifac_size')}")
        elif itype == "SerialInterface":
            lines.append(f"    port = {iface.get('port', '/dev/ttyUSB0')}")
            lines.append(f"    speed = {iface.get('speed', SERIAL_DEFAULT_BAUD)}")
            if iface.get("ifac_size"):
                lines.append(f"    ifac_size = {iface.get('ifac_size')}")
        lines.append("")
    # AutoInterface also binds UDP 4242 — never combine with explicit UDP LAN preset.
    has_udp_lan = any(
        i.get("type") == "UDPInterface" and i.get("enabled", True)
        for i in normalized
    )
    if (
        auto_interface_enabled
        and not has_udp_lan
        and not android
        and sys.platform != "win32"
    ):
        lines.extend([
            "  [[Default Interface]]",
            "    type = AutoInterface",
            "    enabled = Yes",
            "",
        ])
    for name, port, reason in skipped_serial:
        if log:
            log(f"[config] Serial '{name}' skipped — {port}: {reason}")
    return "\n".join(lines).rstrip() + "\n"
