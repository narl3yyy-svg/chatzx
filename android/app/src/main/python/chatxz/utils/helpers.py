import os
import RNS

def get_config_dir():
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return os.path.join(xdg, "chatxz")
    home = os.path.expanduser("~")
    return os.path.join(home, ".config", "chatxz")

def get_data_dir():
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return os.path.join(xdg, "chatxz")
    home = os.path.expanduser("~")
    return os.path.join(home, ".local", "share", "chatxz")

def format_size(size_bytes):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f}TB"

def pretty_hash(h):
    return RNS.hexrep(h) if isinstance(h, bytes) else str(h)

def truncate_hash(hash_str, length=8):
    if len(hash_str) > length:
        return hash_str[:length] + "..."
    return hash_str
