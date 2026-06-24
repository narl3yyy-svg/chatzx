"""Platform detection and storage paths (desktop vs Android/Chaquopy)."""

import os
import sys

_android = None
_files_dir = None
_signals_patched = False


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


def _java_lan_addresses():
    """Enumerate IPv4 LAN addresses via Android/Java network APIs."""
    try:
        from java import jclass
        network_interface = jclass("java.net.NetworkInterface")
        interfaces = network_interface.getNetworkInterfaces()
        found = []
        while interfaces.hasMoreElements():
            iface = interfaces.nextElement()
            if not iface.isUp() or iface.isLoopback():
                continue
            addrs = iface.getInterfaceAddresses()
            while addrs.hasMoreElements():
                ia = addrs.nextElement()
                addr = ia.getAddress()
                host = str(addr.getHostAddress())
                if ":" in host or host.startswith("127.") or host.startswith("169.254."):
                    continue
                broadcast = ia.getBroadcast()
                bcast = str(broadcast.getHostAddress()) if broadcast else None
                found.append((host, bcast))
        return found
    except Exception:
        return []


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


def _linux_iface_link_up(ifname):
    """True when the NIC reports link carrier (cable/Wi-Fi connected)."""
    if not ifname or ifname == "lo":
        return False
    carrier_path = f"/sys/class/net/{ifname}/carrier"
    try:
        with open(carrier_path) as fh:
            return fh.read().strip() == "1"
    except OSError:
        oper_path = f"/sys/class/net/{ifname}/operstate"
        try:
            with open(oper_path) as fh:
                return fh.read().strip() in ("up", "unknown")
        except OSError:
            return False


def _linux_skip_iface(ifname):
    return ifname.startswith(("docker", "br-", "veth", "virbr", "wg", "tun", "tap"))


def _linux_lan_ip():
    """LAN IP from the first link-up interface (ignores stale addresses after unplug)."""
    best = None
    try:
        for ifname in sorted(os.listdir("/sys/class/net")):
            if ifname == "lo" or _linux_skip_iface(ifname):
                continue
            if not _linux_iface_link_up(ifname):
                continue
            ip = _linux_iface_ipv4(ifname)
            if not ip or ip.startswith("169.254."):
                continue
            if not ip.startswith("10.") and not ip.startswith("192.168.") and not ip.startswith("172."):
                best = best or ip
                continue
            return ip
    except OSError:
        pass
    return best


def _linux_enumerate_interfaces():
    entries = []
    try:
        for ifname in sorted(os.listdir("/sys/class/net")):
            if ifname == "lo" or _linux_skip_iface(ifname):
                continue
            link_up = _linux_iface_link_up(ifname)
            ip = _linux_iface_ipv4(ifname) if link_up else None
            if not link_up and not ip:
                continue
            parts = (ip or "").split(".")
            subnet = (
                f"{parts[0]}.{parts[1]}.{parts[2]}.255"
                if len(parts) == 4 and not ip.startswith("169.254.")
                else None
            )
            entries.append({
                "name": ifname,
                "ip": ip if link_up and ip else "disconnected",
                "broadcast": subnet if link_up else None,
                "subnet_broadcast": subnet if link_up else None,
                "up": bool(link_up and ip),
            })
    except OSError:
        pass
    return entries


def lan_connected():
    """True when a physical LAN link is up (carrier), not merely a stale IP."""
    if is_android():
        if _java_lan_addresses():
            return True
        return _android_connectivity_ip() is not None
    return _linux_lan_ip() is not None


def lan_ip():
    """Best-effort LAN IP for direct file transfers (None when unplugged/offline)."""
    import socket

    if is_android():
        for host, _ in _java_lan_addresses():
            return host
        connectivity_ip = _android_connectivity_ip()
        if connectivity_ip:
            return connectivity_ip
        return None

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
    """Active IPv4 interfaces with broadcast addresses."""
    entries = []
    seen = set()

    if is_android():
        for host, bcast in _java_lan_addresses():
            entry = {
                "name": "wifi",
                "ip": host,
                "broadcast": bcast,
                "subnet_broadcast": None,
                "up": True,
            }
            if host and host not in seen:
                seen.add(host)
                parts = host.split(".")
                if len(parts) == 4:
                    entry["subnet_broadcast"] = f"{parts[0]}.{parts[1]}.{parts[2]}.255"
                entries.append(entry)
        if entries:
            return entries
        return entries

    linux_entries = _linux_enumerate_interfaces()
    if linux_entries:
        return linux_entries

    ip = lan_ip()
    if ip:
        parts = ip.split(".")
        subnet = f"{parts[0]}.{parts[1]}.{parts[2]}.255" if len(parts) == 4 else None
        entries.append({
            "name": "default",
            "ip": ip,
            "broadcast": subnet,
            "subnet_broadcast": subnet,
            "up": True,
        })
    return entries


def lan_broadcast():
    """Subnet broadcast address for RNS UDP announces (Android needs directed broadcast)."""
    if is_android():
        for host, bcast in _java_lan_addresses():
            if bcast:
                return bcast

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