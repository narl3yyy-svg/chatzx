"""RNS interface preset management for chatxz config generation."""

import copy
import glob
import os
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


def serial_permission_hint_for_process():
    if user_has_serial_group_access():
        return (
            "Port exists but this chatxz process still cannot open it. "
            "Stop chatxz completely and start it again after logging out/in."
        )
    return SERIAL_PERMISSION_HINT

DEFAULT_INTERFACE_LIST = [
    {
        "id": "udp-lan",
        "preset": "udp_lan",
        "name": "UDP Interface",
        "enabled": True,
        "listen_ip": "0.0.0.0",
        "listen_port": 4242,
        "forward_ip": "255.255.255.255",
        "forward_port": 4242,
        "ifac_size": 16,
    }
]


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


def serial_port_status(port):
    """Return none, missing, permission_denied, or ok."""
    path = (port or "").strip()
    if not path:
        return "none"
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
        return copy.deepcopy(DEFAULT_INTERFACE_LIST)
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
    return out or copy.deepcopy(DEFAULT_INTERFACE_LIST)


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


def list_serial_ports():
    """Return serial devices from pyserial and /dev/ttyUSB* /dev/ttyACM* globs."""
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
        elif preset == "tcp_client" or itype == "TCPClientInterface":
            if "target_host" in updates and updates["target_host"]:
                updated["target_host"] = str(updates["target_host"]).strip()
            if "target_port" in updates and updates["target_port"] is not None:
                updated["target_port"] = int(updates["target_port"])
        elif preset == "udp_lan" or itype == "UDPInterface":
            for key in ("listen_ip", "listen_port", "forward_ip", "forward_port"):
                if key in updates and updates[key] is not None:
                    updated[key] = updates[key]
        out.append(updated)
    if not found:
        raise ValueError(f"Interface not found: {iface_id}")
    return out


def render_rns_config(interfaces, broadcast_ip=None, android=False, log=print):
    lines = [
        "[reticulum]",
        f"enable_transport = {'No' if android else 'Yes'}",
        "share_instance = No",
        "",
        "[logging]",
        "loglevel = 3" if not android else "loglevel = 4",
        "",
        "[interfaces]",
    ]
    skipped_serial = []
    for iface in normalize_interface_list(interfaces):
        itype = iface.get("type", "")
        if itype == "SerialInterface":
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
        elif itype == "TCPClientInterface":
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
    if not android:
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