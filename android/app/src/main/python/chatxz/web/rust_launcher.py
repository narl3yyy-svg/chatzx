"""Start the Rust primary server (used by Android embedded and desktop launcher)."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


def _candidate_bins(root: Path) -> list[Path]:
    name = "chatxz-server.exe" if sys.platform == "win32" else "chatxz-server"
    out = [
        root / "target" / "release" / name,
        root / "target" / "debug" / name,
    ]
    if os.environ.get("CHATXZ_ANDROID") == "1":
        assets = root / "android" / "app" / "src" / "main" / "assets" / "bin" / name
        out.append(assets)
    return out


def find_rust_binary() -> Path | None:
    root = Path(os.environ.get("CHATXZ_ROOT", ".")).resolve()
    for path in _candidate_bins(root):
        if path.is_file():
            return path
    return None


def ensure_android_extracted(src: Path) -> Path:
    """Copy bundled aarch64 binary into app files dir (executable)."""
    base = os.environ.get("CHATXZ_FILES_DIR") or tempfile.gettempdir()
    dst = Path(base) / "chatxz-server"
    if dst.is_file():
        return dst
    shutil.copy2(src, dst)
    mode = dst.stat().st_mode
    dst.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return dst


def start_rust_server(
    public_port: int = 8742,
    backend: str = "http://127.0.0.1:8743",
) -> subprocess.Popen | None:
    bin_path = find_rust_binary()
    if not bin_path:
        print("[rust] chatxz-server binary not found — build with: cargo build --release -p chatxz-server")
        return None
    if os.environ.get("CHATXZ_ANDROID") == "1" and "assets" in str(bin_path):
        bin_path = ensure_android_extracted(bin_path)
    cmd = [
        str(bin_path),
        "--port",
        str(public_port),
        "--backend",
        backend.rstrip("/"),
    ]
    env = os.environ.copy()
    env.setdefault("CHATXZ_ROOT", str(Path(__file__).resolve().parents[2]))
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
        )
        print(f"[rust] started pid={proc.pid} port={public_port}")
        return proc
    except OSError as exc:
        print(f"[rust] failed to start: {exc}")
        return None