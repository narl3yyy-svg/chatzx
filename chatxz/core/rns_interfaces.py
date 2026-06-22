"""RNS interface preset management for chatxz config generation."""

import copy
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
            "enabled": True,
            "port": "/dev/ttyUSB0",
            "speed": 115200,
            "ifac_size": 16,
        },
    },
}

SERIAL_BAUD_RATES = [
    1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600,
]

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
        out.append(merged)
    return out or copy.deepcopy(DEFAULT_INTERFACE_LIST)


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


def list_serial_ports():
    """Return serial devices visible to pyserial (USB/ACM adapters, etc.)."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    ports = []
    try:
        for entry in sorted(list_ports.comports(), key=lambda p: p.device):
            if not _is_useful_serial_port(entry):
                continue
            ports.append({
                "device": entry.device,
                "description": entry.description or "",
                "hwid": entry.hwid or "",
            })
    except Exception:
        return []
    return ports


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
            if "port" in updates and updates["port"]:
                updated["port"] = str(updates["port"]).strip()
            if "speed" in updates and updates["speed"] is not None:
                updated["speed"] = int(updates["speed"])
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


def render_rns_config(interfaces, broadcast_ip=None, android=False):
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
    for iface in normalize_interface_list(interfaces):
        if not iface.get("enabled", True):
            continue
        name = iface.get("name") or iface.get("type", "Interface")
        lines.append(f"  [[{name}]]")
        lines.append(f"    type = {iface.get('type', 'UDPInterface')}")
        lines.append("    enabled = Yes")
        itype = iface.get("type", "")
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
            lines.append(f"    speed = {iface.get('speed', 115200)}")
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
    return "\n".join(lines).rstrip() + "\n"