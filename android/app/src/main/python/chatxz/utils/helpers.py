import os

from chatxz.utils.platform import is_android, storage_root


def get_config_dir():
    if is_android():
        return storage_root()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return os.path.join(xdg, "chatxz")
    return os.path.join(os.path.expanduser("~"), ".config", "chatxz")


def get_data_dir():
    if is_android():
        return os.path.join(storage_root(), "data")
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return os.path.join(xdg, "chatxz")
    return os.path.join(os.path.expanduser("~"), ".local", "share", "chatxz")

def format_size(size_bytes):
    if size_bytes < 0:
        size_bytes = 0
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size_bytes < 1024:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f}TB"


def format_speed(bytes_per_sec):
    return format_size(bytes_per_sec) + "/s"

def truncate_hash(hash_str, length=8):
    if len(hash_str) > length:
        return hash_str[:length] + "..."
    return hash_str


IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v", ".ogv", ".mpeg", ".mpg"})


def media_type_for_filename(filename):
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    return "file"
