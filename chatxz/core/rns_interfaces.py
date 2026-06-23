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
    try:
        from chatxz.utils.platform import is_android
        if is_android():
            return copy.deepcopy(ANDROID_DEFAULT_INTERFACE_LIST)
    except Exception:
        pass
    if (
        sys.platform in ("win32", "darwin")
        or os.environ.get("CHATXZ_PORTABLE")
        or getattr(sys, "frozen", False)
    ):
        return copy.deepcopy(ANDROID_DEFAULT_INTERFACE_LIST)
    return copy.deepcopy(DEFAULT_INTERFACE_LIST)


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
    if len(items) != 1:
        return False
    only = items[0]
    if only.get("type") != "TCPClientInterface":
        return False
    host = (only.get("target_host") or "").strip().lower()
    return host in ("127.0.0.1", "localhost", "")


def _new_id():
    return uuid.uuid4().hex[:8]


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
    return out or default_interface_list()


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
        if preset == "serial" or itype == "SerialInterface":
            if "port" in updates:
                updated["port"] = str(updates["port"] or "").strip()
            if "speed" in updates and updates["speed"] is not None:
                updated["speed"] = int(updates["speed"])
            if "enabled" in updates:
                updated["enabled"] = bool(updates["enabled"])
            updated = _sync_serial_enabled(updated)
            if serial_port_accessible(updated.get("port")):
                updated["enabled"] = True
        elif preset in ("tcp_client", "tcp_server") or itype in ("TCPClientInterface", "TCPServerInterface"):
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
        print(f"[serial] Hot-added RNS SerialInterface on {port}")
        for attempt in range(3):
            try:
                RNS.Transport.identity.announce()
            except Exception:
                pass
            if attempt < 2:
                time.sleep(0.4)
        return iface
    except Exception as exc:
        print(f"[serial] Hot-add failed for {port}: {exc}")
        return None


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
        return hot_add_serial_interface(port, speed=speed)
    return None


def render_rns_config(interfaces, broadcast_ip=None, android=False, log=print):
    normalized = normalize_interface_list(interfaces)
    has_tcp_server = any(
        i.get("type") == "TCPServerInterface" and i.get("enabled", True)
        for i in normalized
    )
    enable_transport = "Yes" if (not android or has_tcp_server) else "No"
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
    for iface in normalized:
        itype = iface.get("type", "")
        if itype == "SerialInterface":
            if android:
                port, reason = serial_skip_reason(iface.get("port"))
                skipped_serial.append((iface.get("name") or "Serial", port, "hot-add on Android"))
                continue
            if not serial_runtime_active(iface):
                port, reason = serial_skip_reason(iface.get("port"))
                skipped_serial.append((iface.get("name") or "Serial", port, reason))
                continue
        elif not iface.get("enabled", True):
            continue
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
    # AutoInterface uses multicast and is unreliable on Windows (firewall / virtual adapters).
    if not android and sys.platform != "win32":
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
