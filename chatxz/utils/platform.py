"""Platform detection and storage paths (desktop vs Android/Chaquopy)."""

import json
import os
import re
import subprocess
import sys
import threading
import time

_android = None
_files_dir = None
_signals_patched = False
_lan_interface_pref = None
_desktop_if_cache = {"entries": None, "expires": 0.0}
_desktop_if_cache_lock = threading.Lock()
DESKTOP_IF_CACHE_TTL = 45.0


def _subprocess_flags():
    if sys.platform == "win32":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def invalidate_desktop_interface_cache():
    with _desktop_if_cache_lock:
        _desktop_if_cache["entries"] = None
        _desktop_if_cache["expires"] = 0.0


def patch_embedded_signals():
    """RNS registers SIG handlers; Android/Chaquopy runs Python off the main interpreter thread."""
    global _signals_patched
    if _signals_patched:
        return
    import signal

    _real_signal = signal.signal

    def _safe_signal(signum, handler):
        try:
            return _real_signal(signum, handler)
        except ValueError as exc:
            if "signal only works in main thread" in str(exc):
                return None
            raise

    signal.signal = _safe_signal
    _signals_patched = True


def is_android():
    """True on Chaquopy/Android — never cache False before env/java checks run."""
    global _android
    if os.environ.get("CHATXZ_ANDROID") == "1":
        _android = True
        return True
    if _android is True:
        return True
    if "chaquopy" in sys.modules:
        _android = True
        return True
    try:
        from java import jclass
        jclass("com.chaquo.python.android.AndroidPlatform")
        _android = True
        return True
    except Exception:
        pass
    _android = False
    return False


def android_files_dir():
    global _files_dir
    if _files_dir:
        return _files_dir
    env = os.environ.get("CHATXZ_FILES_DIR")
    if env:
        _files_dir = env
        return _files_dir
    try:
        from java import jclass
        Python = jclass("com.chaquo.python.Python")
        ctx = Python.getPlatform().getApplication()
        _files_dir = str(ctx.getFilesDir().getAbsolutePath())
        return _files_dir
    except Exception:
        return None


def storage_root():
    """Writable root for config, data, and received files."""
    if is_android():
        base = android_files_dir() or os.path.expanduser("~")
        return os.path.join(base, "chatxz")
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return os.path.join(xdg, "chatxz")
    return os.path.join(os.path.expanduser("~"), ".config", "chatxz")


def _android_is_vpn_iface(name):
    """True for common Android VPN/tunnel interface names."""
    low = (name or "").lower()
    if _linux_is_tunnel_iface(name):
        return True
    if low.startswith(("vpn", "ppp", "ccmni", "rmnet", "clat", "ipsec")):
        return True
    return False


def _android_connectivity_interfaces():
    """Enumerate all Android networks including VPN via ConnectivityManager."""
    by_name = {}
    try:
        from java import jclass
        python = jclass("com.chaquo.python.Python")
        ctx = python.getPlatform().getApplication()
        context = jclass("android.content.Context")
        cm = ctx.getSystemService(context.CONNECTIVITY_SERVICE)
        if cm is None:
            return []
        caps_cls = jclass("android.net.NetworkCapabilities")
        transport_vpn = int(caps_cls.TRANSPORT_VPN)
        transport_wifi = int(caps_cls.TRANSPORT_WIFI)
        transport_cell = int(caps_cls.TRANSPORT_CELLULAR)
        transport_eth = int(caps_cls.TRANSPORT_ETHERNET)
        for network in cm.getAllNetworks():
            caps = cm.getNetworkCapabilities(network)
            props = cm.getLinkProperties(network)
            if caps is None or props is None:
                continue
            ifname = str(props.getInterfaceName() or "").strip()
            if not ifname:
                continue
            if caps.hasTransport(transport_vpn):
                kind = "vpn"
            elif caps.hasTransport(transport_wifi):
                kind = "wifi"
            elif caps.hasTransport(transport_eth):
                kind = "ethernet"
            elif caps.hasTransport(transport_cell):
                kind = "cellular"
            else:
                kind = "vpn" if _android_is_vpn_iface(ifname) else "other"
            ip = None
            addrs = props.getLinkAddresses()
            if addrs is not None:
                it = addrs.iterator()
                while it.hasNext():
                    la = it.next()
                    addr = la.getAddress()
                    if addr is None:
                        continue
                    host = str(addr.getHostAddress())
                    if ":" in host or host.startswith("127.") or host.startswith("169.254."):
                        continue
                    ip = host
                    break
            parts = (ip or "").split(".")
            subnet = (
                f"{parts[0]}.{parts[1]}.{parts[2]}.255"
                if len(parts) == 4 else None
            )
            by_name[ifname] = {
                "name": ifname,
                "kind": kind,
                "ip": ip if ip else "disconnected",
                "broadcast": subnet,
                "subnet_broadcast": subnet,
                "up": bool(ip),
            }
    except Exception:
        pass
    return [by_name[k] for k in sorted(by_name)]


def _android_connectivity_ip():
    """Best-effort LAN IP via Android ConnectivityManager."""
    try:
        from java import jclass
        Python = jclass("com.chaquo.python.Python")
        ctx = Python.getPlatform().getApplication()
        connectivity = jclass("android.net.ConnectivityManager")
        cm = ctx.getSystemService(connectivity.CONNECTIVITY_SERVICE)
        if cm is None:
            return None
        network = cm.getActiveNetwork()
        if network is None:
            return None
        props = cm.getLinkProperties(network)
        if props is None:
            return None
        addrs = props.getLinkAddresses()
        if addrs is None:
            return None
        it = addrs.iterator()
        while it.hasNext():
            la = it.next()
            addr = la.getAddress()
            if addr is None:
                continue
            host = str(addr.getHostAddress())
            if ":" in host or host.startswith("127.") or host.startswith("169.254."):
                continue
            return host
    except Exception:
        pass
    return None


def set_lan_interface_preference(ifname):
    """Pin LAN discovery/chat to one NIC (empty/None = auto)."""
    global _lan_interface_pref
    name = (ifname or "").strip()
    _lan_interface_pref = name or None
    invalidate_desktop_interface_cache()


def get_lan_interface_preference():
    return _lan_interface_pref


def load_lan_interface_preference(config_dir=None):
    try:
        from chatxz.utils.helpers import get_config_dir
        import json

        root = config_dir or get_config_dir()
        path = os.path.join(root, "settings.json")
        with open(path, encoding="utf-8") as fh:
            return (json.load(fh).get("lan_interface") or "").strip() or None
    except Exception:
        return None


def apply_lan_interface_preference(config_dir=None):
    set_lan_interface_preference(load_lan_interface_preference(config_dir))


def _java_enumerate_interfaces():
    """Enumerate IPv4 LAN interfaces via Android/Java network APIs."""
    try:
        from java import jclass
        network_interface = jclass("java.net.NetworkInterface")
        interfaces = network_interface.getNetworkInterfaces()
        by_name = {}
        while interfaces.hasMoreElements():
            iface = interfaces.nextElement()
            if iface.isLoopback():
                continue
            name = str(iface.getName())
            up = bool(iface.isUp())
            addrs = iface.getInterfaceAddresses()
            while addrs.hasMoreElements():
                ia = addrs.nextElement()
                addr = ia.getAddress()
                host = str(addr.getHostAddress())
                if ":" in host or host.startswith("127.") or host.startswith("169.254."):
                    continue
                broadcast = ia.getBroadcast()
                bcast = str(broadcast.getHostAddress()) if broadcast else None
                parts = host.split(".")
                subnet = (
                    f"{parts[0]}.{parts[1]}.{parts[2]}.255"
                    if len(parts) == 4 else None
                )
                kind = "vpn" if _android_is_vpn_iface(name) else (
                    "wifi" if name.lower().startswith(("wl", "wlan", "wifi")) else
                    "ethernet" if name.lower().startswith(("en", "eth")) else "other"
                )
                by_name[name] = {
                    "name": name,
                    "kind": kind,
                    "ip": host if up else "disconnected",
                    "broadcast": bcast if up else None,
                    "subnet_broadcast": subnet if up else None,
                    "up": up,
                }
        return [by_name[k] for k in sorted(by_name)]
    except Exception:
        return []


def _java_lan_addresses():
    found = []
    for entry in _java_enumerate_interfaces():
        if entry.get("up") and entry.get("ip") and entry["ip"] != "disconnected":
            found.append((entry["ip"], entry.get("broadcast")))
    return found


def _linux_iface_ipv4(ifname):
    """IPv4 address assigned to a Linux network interface, or None."""
    import fcntl
    import socket
    import struct

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        ifreq = struct.pack("256s", ifname.encode()[:15])
        res = fcntl.ioctl(sock.fileno(), 0x8915, ifreq)  # SIOCGIFADDR
        ip = socket.inet_ntoa(res[20:24])
        sock.close()
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    return None


def _linux_iface_operstate(ifname):
    try:
        with open(f"/sys/class/net/{ifname}/operstate") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _linux_iface_link_up(ifname):
    """True when the NIC reports link carrier (cable/Wi-Fi connected)."""
    if not ifname or ifname == "lo":
        return False
    carrier_path = f"/sys/class/net/{ifname}/carrier"
    try:
        with open(carrier_path) as fh:
            return fh.read().strip() == "1"
    except OSError:
        return _linux_iface_operstate(ifname) in ("up", "unknown")


def _linux_is_tunnel_iface(ifname):
    """VPN/tunnel interfaces users may want to pin for RNS/LAN (WireGuard, OpenVPN, etc.)."""
    name = (ifname or "").lower()
    if name.startswith((
        "tun", "tap", "wg", "ppp", "tailscale", "nordlynx", "proton", "zt",
        "zerotier", "ts", "utun",
    )):
        return True
    try:
        with open(f"/sys/class/net/{ifname}/type") as fh:
            # ARPHRD_NONE (65534) and PPP (512) are common for VPN tunnels.
            return int(fh.read().strip()) in (512, 65534)
    except (OSError, ValueError):
        return False


def _linux_skip_iface(ifname):
    """Skip container bridge internals — not user-selectable host NICs."""
    if not ifname or ifname == "lo":
        return True
    if ifname in ("docker0",):
        return True
    return ifname.startswith(("veth", "virbr", "ifb", "dummy"))


def _linux_iface_broadcast(ip):
    parts = (ip or "").split(".")
    if len(parts) != 4 or ip.startswith("169.254."):
        return None
    return f"{parts[0]}.{parts[1]}.{parts[2]}.255"


def _linux_iface_usable(ifname):
    """True when an interface can carry LAN/RNS traffic."""
    if not ifname or ifname == "lo" or _linux_skip_iface(ifname):
        return False
    ip = _linux_iface_ipv4(ifname)
    if ip and not ip.startswith("169.254."):
        if _linux_is_tunnel_iface(ifname):
            return True
        return _linux_iface_link_up(ifname)
    if _linux_is_tunnel_iface(ifname):
        return _linux_iface_operstate(ifname) in ("up", "unknown")
    return _linux_iface_link_up(ifname)


def _linux_iface_kind(ifname):
    if _linux_is_tunnel_iface(ifname):
        return "vpn"
    low = (ifname or "").lower()
    if low.startswith(("wl", "wlan", "wifi")):
        return "wifi"
    if low.startswith(("en", "eth")):
        return "ethernet"
    return "other"


def _linux_iface_entry(ifname):
    tunnel = _linux_is_tunnel_iface(ifname)
    ip = _linux_iface_ipv4(ifname)
    usable = _linux_iface_usable(ifname)
    subnet = _linux_iface_broadcast(ip) if usable and ip and not tunnel else None
    return {
        "name": ifname,
        "kind": _linux_iface_kind(ifname),
        "ip": ip if usable and ip else "disconnected",
        "broadcast": subnet if usable else None,
        "subnet_broadcast": subnet if usable else None,
        "up": bool(usable and ip),
    }


def _linux_lan_ip_from_name(ifname):
    if not ifname or ifname == "lo" or _linux_skip_iface(ifname):
        return None
    if not _linux_iface_usable(ifname):
        return None
    ip = _linux_iface_ipv4(ifname)
    if ip and not ip.startswith("169.254."):
        return ip
    return None


def _linux_iface_auto_priority(ifname, ip):
    """Higher = preferred in auto mode. Physical LAN beats VPN tunnels."""
    if _linux_is_tunnel_iface(ifname):
        return 10
    if ip.startswith("169.254."):
        return 20
    if ip.startswith(("10.", "192.168.", "172.")):
        return 100
    return 50


def _linux_lan_ip():
    """LAN IP from preferred or best link-up interface (physical LAN before VPN)."""
    pref = get_lan_interface_preference()
    if pref:
        return _linux_lan_ip_from_name(pref)

    best_ip = None
    best_score = -1
    try:
        for ifname in sorted(os.listdir("/sys/class/net")):
            if ifname == "lo" or _linux_skip_iface(ifname):
                continue
            ip = _linux_lan_ip_from_name(ifname)
            if not ip:
                continue
            score = _linux_iface_auto_priority(ifname, ip)
            if score > best_score:
                best_score = score
                best_ip = ip
    except OSError:
        pass
    return best_ip


def _linux_enumerate_interfaces():
    entries = []
    try:
        for ifname in sorted(os.listdir("/sys/class/net")):
            if ifname == "lo" or _linux_skip_iface(ifname):
                continue
            entries.append(_linux_iface_entry(ifname))
    except OSError:
        pass
    return entries


def _host_ipv4_broadcast(ip):
    parts = (ip or "").split(".")
    if len(parts) != 4 or ip.startswith("169.254."):
        return None
    return f"{parts[0]}.{parts[1]}.{parts[2]}.255"


def _windows_is_vpn_iface(name):
    low = (name or "").lower()
    return any(token in low for token in (
        "wireguard", "wg", "tailscale", "nordlynx", "openvpn", "tap", "tun",
        "zerotier", "zt", "proton", "vpn", "wintun",
    ))


def _windows_iface_kind(name):
    if _windows_is_vpn_iface(name):
        return "vpn"
    low = (name or "").lower()
    if "wi-fi" in low or "wifi" in low or "wireless" in low or low.startswith("wlan"):
        return "wifi"
    if "ethernet" in low or low.startswith("eth"):
        return "ethernet"
    return "other"


def _windows_default_gateway_subnet():
    """Return 'a.b.c' subnet prefix for the active default route, if known."""
    try:
        proc = subprocess.run(
            ["route", "print", "0.0.0.0"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
            creationflags=_subprocess_flags(),
        )
        for line in (proc.stdout or "").splitlines():
            if "0.0.0.0" not in line:
                continue
            parts = line.split()
            nums = [p for p in parts if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", p)]
            if len(nums) >= 2 and nums[0] == "0.0.0.0":
                gw = nums[1]
                gw_parts = gw.split(".")
                if len(gw_parts) == 4 and not gw.startswith("0."):
                    return ".".join(gw_parts[:3])
    except Exception:
        pass
    return None


def _windows_enumerate_interfaces_ipconfig():
    """Fast Windows NIC scan via ipconfig (no PowerShell startup)."""
    try:
        proc = subprocess.run(
            ["ipconfig"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
            creationflags=_subprocess_flags(),
        )
        text = proc.stdout or ""
    except Exception:
        return []

    gw_subnet = _windows_default_gateway_subnet()
    entries = []
    current_name = None
    current_up = True
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not line.startswith((" ", "\t")) and stripped.endswith(":"):
            current_name = stripped[:-1].strip()
            current_up = True
            continue
        if not current_name:
            continue
        low = stripped.lower()
        if "media disconnected" in low:
            current_up = False
            continue
        if any(token in low for token in ("subnet mask", "default gateway", "subnetmask", "gateway")):
            continue
        match = re.search(
            r"IPv4 Address[^:]*:\s*([\d.]+)",
            stripped,
            flags=re.IGNORECASE,
        )
        if not match:
            match = re.search(
                r":\s*(\d{1,3}(?:\.\d{1,3}){3})\s*(?:\(Preferred\))?\s*$",
                stripped,
            )
        if not match:
            continue
        ip = match.group(1)
        if ip.startswith("127.") or ip.startswith("169.254."):
            continue
        subnet = _host_ipv4_broadcast(ip) if current_up else None
        ip_parts = ip.split(".")
        gateway_iface = bool(
            gw_subnet
            and len(ip_parts) == 4
            and ".".join(ip_parts[:3]) == gw_subnet
        )
        entries.append({
            "name": current_name,
            "kind": _windows_iface_kind(current_name),
            "ip": ip if current_up else "disconnected",
            "broadcast": subnet if current_up else None,
            "subnet_broadcast": subnet if current_up else None,
            "up": current_up,
            "gateway_iface": gateway_iface,
        })
    return entries


def _windows_enumerate_interfaces_powershell():
    """Windows NIC scan via Get-NetAdapter — includes all adapters (even without IPv4)."""
    script = r"""
$gwIndex = $null
$route = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
  Where-Object { $_.NextHop -and $_.NextHop -ne '0.0.0.0' } |
  Sort-Object RouteMetric, InterfaceMetric |
  Select-Object -First 1
if ($route) { $gwIndex = $route.InterfaceIndex }
$rows = @()
Get-NetAdapter -ErrorAction SilentlyContinue | ForEach-Object {
  $adapter = $_
  $ip = $null
  Get-NetIPAddress -InterfaceIndex $adapter.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' } |
    ForEach-Object { if (-not $ip) { $ip = $_.IPAddress } }
  $up = ($adapter.Status -eq 'Up')
  [PSCustomObject]@{
    name = $adapter.Name
    ip = $(if ($ip) { $ip } else { $null })
    up = $up
    gateway_iface = ($adapter.InterfaceIndex -eq $gwIndex)
  }
}
if ($rows.Count -eq 0) { '[]' } else { $rows | ConvertTo-Json -Compress }
"""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            creationflags=_subprocess_flags(),
        )
        raw = (proc.stdout or "").strip()
        if not raw:
            return []
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
    except Exception:
        return []

    entries = []
    for row in data or []:
        name = str(row.get("name") or "").strip()
        ip = str(row.get("ip") or "").strip()
        up = bool(row.get("up"))
        if not name:
            continue
        subnet = _host_ipv4_broadcast(ip) if up and ip else None
        entries.append({
            "name": name,
            "kind": _windows_iface_kind(name),
            "ip": ip if up and ip else "disconnected",
            "broadcast": subnet if up and ip else None,
            "subnet_broadcast": subnet if up and ip else None,
            "up": up and bool(ip),
            "gateway_iface": bool(row.get("gateway_iface")),
        })
    return entries


def _windows_merge_interface_entries(*groups):
    """Merge interface lists without dropping multi-homed or duplicate-name NICs."""
    merged = []
    seen = set()
    for entries in groups:
        for entry in entries or []:
            name = entry.get("name") or ""
            ip = entry.get("ip") or "disconnected"
            key = (name, ip)
            if not name or key in seen:
                continue
            seen.add(key)
            merged.append(entry)
    return merged


def _windows_enumerate_interfaces():
    ipconfig_entries = _windows_enumerate_interfaces_ipconfig()
    ps_entries = _windows_enumerate_interfaces_powershell()
    if ps_entries:
        by_name = {e.get("name"): e for e in ps_entries if e.get("name")}
        for entry in ipconfig_entries:
            name = entry.get("name")
            if not name:
                continue
            prev = by_name.get(name)
            if not prev:
                by_name[name] = entry
                continue
            if entry.get("gateway_iface") and not prev.get("gateway_iface"):
                by_name[name] = {**prev, **entry, "gateway_iface": True}
            elif entry.get("up") and not prev.get("up"):
                by_name[name] = {**prev, **entry}
        return [by_name[k] for k in sorted(by_name)]
    return ipconfig_entries


def _darwin_is_vpn_iface(name):
    low = (name or "").lower()
    return any(token in low for token in (
        "utun", "tun", "tap", "ppp", "ipsec", "wg", "tailscale", "zerotier", "vpn",
    ))


def _darwin_iface_kind(name):
    if _darwin_is_vpn_iface(name):
        return "vpn"
    low = (name or "").lower()
    if low.startswith(("en", "eth")):
        return "ethernet"
    if low.startswith(("wl", "wlan", "wifi")):
        return "wifi"
    return "other"


def _darwin_enumerate_interfaces():
    """Enumerate IPv4 interfaces on macOS via ifconfig."""
    try:
        proc = subprocess.run(
            ["ifconfig"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        text = proc.stdout or ""
    except Exception:
        return []

    entries = []
    current = None
    current_up = False
    current_active = False
    for line in text.splitlines():
        if line and not line.startswith(("\t", " ")):
            current = line.split(":")[0].strip()
            current_up = "<UP" in line or "UP," in line or ",UP>" in line
            current_active = False
            continue
        if not current or current == "lo0":
            continue
        stripped = line.strip()
        if stripped.lower().startswith("status:") and "active" in stripped.lower():
            current_active = True
            continue
        match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", line)
        if not match:
            continue
        ip = match.group(1)
        if ip.startswith("127.") or ip.startswith("169.254."):
            continue
        up = current_up or current_active
        subnet = _host_ipv4_broadcast(ip) if up else None
        entries.append({
            "name": current,
            "kind": _darwin_iface_kind(current),
            "ip": ip if up else "disconnected",
            "broadcast": subnet if up else None,
            "subnet_broadcast": subnet if up else None,
            "up": up,
        })
    return entries


def _desktop_enumerate_interfaces_uncached():
    if sys.platform == "win32":
        entries = _windows_enumerate_interfaces()
        if entries:
            return entries
    elif sys.platform == "darwin":
        entries = _darwin_enumerate_interfaces()
        if entries:
            return entries
    return _linux_enumerate_interfaces()


def _desktop_enumerate_interfaces():
    now = time.time()
    with _desktop_if_cache_lock:
        cached = _desktop_if_cache.get("entries")
        if cached is not None and now < _desktop_if_cache.get("expires", 0):
            return list(cached)
    entries = _desktop_enumerate_interfaces_uncached()
    with _desktop_if_cache_lock:
        _desktop_if_cache["entries"] = list(entries)
        _desktop_if_cache["expires"] = now + DESKTOP_IF_CACHE_TTL
    return entries


def _desktop_iface_auto_priority(entry):
    if entry.get("kind") == "vpn":
        return 10
    ip = str(entry.get("ip") or "")
    if ip.startswith("169.254."):
        return 20
    if entry.get("gateway_iface"):
        return 120
    if ip.startswith(("10.", "192.168.", "172.")):
        return 100
    return 50


def _desktop_lan_ip_from_name(ifname):
    for entry in _desktop_enumerate_interfaces():
        if entry.get("name") != ifname:
            continue
        if entry.get("up") and entry.get("ip") not in (None, "disconnected"):
            ip = str(entry.get("ip") or "")
            if not ip.startswith("169.254."):
                return ip
    return None


def _desktop_lan_ip():
    pref = get_lan_interface_preference()
    if pref:
        return _desktop_lan_ip_from_name(pref)

    best_ip = None
    best_score = -1
    for entry in _desktop_enumerate_interfaces():
        if entry.get("kind") == "vpn":
            continue
        if not entry.get("up"):
            continue
        ip = entry.get("ip")
        if not ip or ip == "disconnected" or str(ip).startswith("169.254."):
            continue
        score = _desktop_iface_auto_priority(entry)
        if score > best_score:
            best_score = score
            best_ip = ip
    return best_ip


def enumerate_lan_interfaces():
    """All local NICs for the LAN interface picker (ignores preference)."""
    if is_android():
        by_name = {}
        for entry in _android_connectivity_interfaces():
            name = entry.get("name")
            if name:
                by_name[name] = entry
        for entry in _java_enumerate_interfaces():
            name = entry.get("name")
            if not name:
                continue
            prev = by_name.get(name)
            if prev:
                merged = {**prev, **entry}
                if entry.get("ip") and entry.get("ip") != "disconnected":
                    merged["ip"] = entry["ip"]
                if prev.get("kind") == "vpn" or entry.get("kind") == "vpn":
                    merged["kind"] = "vpn"
                by_name[name] = merged
            else:
                by_name[name] = entry
        entries = [by_name[k] for k in sorted(by_name)]
        if not entries:
            ip = _android_connectivity_ip()
            if ip:
                parts = ip.split(".")
                subnet = (
                    f"{parts[0]}.{parts[1]}.{parts[2]}.255"
                    if len(parts) == 4 else None
                )
                entries = [{
                    "name": "active",
                    "kind": "wifi",
                    "ip": ip,
                    "broadcast": subnet,
                    "subnet_broadcast": subnet,
                    "up": True,
                }]
        else:
            seen_ips = {
                e.get("ip") for e in entries
                if e.get("ip") and e.get("ip") != "disconnected"
            }
            ip = _android_connectivity_ip()
            if ip and ip not in seen_ips:
                parts = ip.split(".")
                subnet = (
                    f"{parts[0]}.{parts[1]}.{parts[2]}.255"
                    if len(parts) == 4 else None
                )
                entries.append({
                    "name": "active",
                    "kind": "wifi",
                    "ip": ip,
                    "broadcast": subnet,
                    "subnet_broadcast": subnet,
                    "up": True,
                })
        return entries
    if sys.platform in ("win32", "darwin"):
        return _desktop_enumerate_interfaces()
    return _linux_enumerate_interfaces()


def _filter_interfaces_for_lan(entries):
    """Restrict LAN beacon/chat to the user-selected interface when set."""
    pref = get_lan_interface_preference()
    if not pref:
        return entries
    for entry in entries:
        if entry.get("name") == pref:
            return [entry]
    return [{
        "name": pref,
        "ip": "disconnected",
        "broadcast": None,
        "subnet_broadcast": None,
        "up": False,
    }]


def physical_lan_reachable():
    """True when a non-VPN NIC has link and IPv4 (RJ45/Wi-Fi), not VPN-only."""
    if is_android():
        for entry in _java_enumerate_interfaces():
            if entry.get("kind") == "vpn":
                continue
            if entry.get("up") and entry.get("ip") not in (None, "disconnected"):
                if not str(entry.get("ip", "")).startswith("169.254."):
                    return True
        return False
    entries = (
        _desktop_enumerate_interfaces()
        if sys.platform in ("win32", "darwin")
        else _linux_enumerate_interfaces()
    )
    for entry in entries:
        if entry.get("kind") == "vpn":
            continue
        if entry.get("up") and entry.get("ip") not in (None, "disconnected"):
            if not str(entry.get("ip", "")).startswith("169.254."):
                return True
    return False


def lan_connected():
    """True when a physical LAN link is up (carrier), not merely a stale IP."""
    if is_android():
        pref = get_lan_interface_preference()
        if pref:
            for entry in _java_enumerate_interfaces():
                if entry.get("name") == pref:
                    return bool(entry.get("up"))
            return False
        if _java_lan_addresses():
            return True
        return _android_connectivity_ip() is not None
    if sys.platform in ("win32", "darwin"):
        return _desktop_lan_ip() is not None
    return _linux_lan_ip() is not None


def lan_ip():
    """Best-effort LAN IP for direct file transfers (None when unplugged/offline)."""
    import socket

    if is_android():
        pref = get_lan_interface_preference()
        for entry in _java_enumerate_interfaces():
            if pref and entry.get("name") != pref:
                continue
            if entry.get("up") and entry.get("ip") and entry["ip"] != "disconnected":
                return entry["ip"]
        if pref:
            return None
        connectivity_ip = _android_connectivity_ip()
        if connectivity_ip:
            return connectivity_ip
        return None

    if sys.platform in ("win32", "darwin"):
        ip = _desktop_lan_ip()
        if ip:
            return ip
    else:
        ip = _linux_lan_ip()
        if ip:
            return ip

    if not lan_connected():
        return None

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.5)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    return None


def list_network_interfaces():
    """IPv4 interfaces used for LAN beacon/RNS (respects lan_interface preference)."""
    if is_android():
        return _filter_interfaces_for_lan(_java_enumerate_interfaces())

    if sys.platform in ("win32", "darwin"):
        desktop_entries = _desktop_enumerate_interfaces()
        if desktop_entries:
            return _filter_interfaces_for_lan(desktop_entries)

    linux_entries = _linux_enumerate_interfaces()
    if linux_entries:
        return _filter_interfaces_for_lan(linux_entries)

    ip = lan_ip()
    if ip:
        parts = ip.split(".")
        subnet = f"{parts[0]}.{parts[1]}.{parts[2]}.255" if len(parts) == 4 else None
        name = get_lan_interface_preference() or "default"
        return [{
            "name": name,
            "ip": ip,
            "broadcast": subnet,
            "subnet_broadcast": subnet,
            "up": True,
        }]
    return []


def desktop_lan_status():
    """Single-pass LAN status for API handlers (reuses cached interface list)."""
    entries = _desktop_enumerate_interfaces()
    pref = get_lan_interface_preference()
    physical = False
    connected = False
    best_ip = None
    best_score = -1
    broadcast = None

    for entry in entries:
        if entry.get("kind") == "vpn":
            continue
        if not entry.get("up"):
            continue
        ip = entry.get("ip")
        if not ip or ip == "disconnected" or str(ip).startswith("169.254."):
            continue
        physical = True
        if pref and entry.get("name") != pref:
            continue
        connected = True
        score = _desktop_iface_auto_priority(entry)
        if score > best_score:
            best_score = score
            best_ip = str(ip)
            broadcast = entry.get("broadcast") or entry.get("subnet_broadcast")

    if best_ip and not broadcast:
        broadcast = _host_ipv4_broadcast(best_ip)

    return {
        "physical_lan_reachable": physical,
        "lan_connected": connected,
        "lan_ip": best_ip,
        "broadcast": broadcast or "255.255.255.255",
        "interfaces": entries,
    }


def local_ipv4_addresses():
    """All non-loopback IPv4 addresses on this host."""
    found = set()
    for entry in enumerate_lan_interfaces():
        ip = entry.get("ip")
        if entry.get("up") and ip and ip != "disconnected":
            if not str(ip).startswith(("127.", "169.254.")):
                found.add(str(ip))
    if found:
        return sorted(found)
    ip = lan_ip()
    return [ip] if ip else []


def lan_broadcast():
    """Subnet broadcast address for RNS UDP announces (Android needs directed broadcast)."""
    for iface in list_network_interfaces():
        if iface.get("up") and iface.get("broadcast"):
            return iface["broadcast"]
        if iface.get("up") and iface.get("subnet_broadcast"):
            return iface["subnet_broadcast"]

    ip = lan_ip()
    if ip:
        parts = ip.split(".")
        if len(parts) == 4:
            return f"{parts[0]}.{parts[1]}.{parts[2]}.255"
    return "255.255.255.255"


def android_storage_dirs():
    """Writable folder choices for the Android received-files setting."""
    root = storage_root()
    dirs = [
        {"label": "Received (default)", "path": os.path.join(root, "received")},
        {"label": "Downloads (app)", "path": os.path.join(root, "downloads")},
    ]
    try:
        from java import jclass
        environment = jclass("android.os.Environment")
        downloads = environment.getExternalStoragePublicDirectory(environment.DIRECTORY_DOWNLOADS)
        if downloads is not None:
            path = str(downloads.getAbsolutePath())
            if path:
                dirs.append({"label": "Phone Downloads", "path": path})
        documents = environment.getExternalStoragePublicDirectory(environment.DIRECTORY_DOCUMENTS)
        if documents is not None:
            path = str(documents.getAbsolutePath())
            if path:
                dirs.append({"label": "Documents", "path": path})
    except Exception:
        pass
    seen = set()
    unique = []
    for entry in dirs:
        path = os.path.normpath(entry["path"])
        if path in seen:
            continue
        seen.add(path)
        unique.append({"label": entry["label"], "path": path})
    return unique