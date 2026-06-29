#!/usr/bin/env python3
"""Install native voice dependencies (libopus + pyaudio) for chatxz run scripts."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

NUGET_OPUS_URL = "https://www.nuget.org/api/v2/package/DSharpPlus.VoiceNext.Natives/1.0.0"
WINDOWS_DLL_NAMES = ("libopus.dll", "opus.dll", "libopus-0.dll")
MACOS_DYLIB_NAMES = ("libopus.0.dylib", "libopus.dylib")


def repo_root() -> Path:
    env = os.environ.get("CHATXZ_ROOT", "").strip()
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parent.parent


def native_dir(root: Path) -> Path:
    if sys.platform == "win32":
        return root / "chatxz" / "core" / "native" / "windows"
    if sys.platform == "darwin":
        return root / "chatxz" / "core" / "native" / "macos"
    return root / "chatxz" / "core" / "native" / "linux"


def voice_ready(py: str, root: Path) -> bool:
    env = os.environ.copy()
    env.setdefault("CHATXZ_ROOT", str(root))
    env.setdefault("PYTHONPATH", str(root))
    cmd = (
        "from chatxz.core.audio import call_audio_available; "
        "import sys; sys.exit(0 if call_audio_available() else 1)"
    )
    return subprocess.run([py, "-c", cmd], env=env, cwd=str(root)).returncode == 0


def pip_install_pyaudio(py: str) -> bool:
    log = repo_root() / ".voice-install.log"
    with log.open("w", encoding="utf-8") as fh:
        ok = subprocess.run(
            [py, "-m", "pip", "install", "-q", "pyaudio"],
            stdout=fh,
            stderr=subprocess.STDOUT,
        ).returncode == 0
    if ok:
        log.unlink(missing_ok=True)
    return ok


def windows_runtime_arch() -> str:
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64", "x64"):
        return "win-x64"
    if machine in ("x86", "i386", "i686"):
        return "win-x86"
    if machine in ("arm64", "aarch64"):
        return "win-arm64"
    return "win-x64"


def _pick_nupkg_dll(names: list[str], arch: str) -> str | None:
    preferred = [
        f"runtimes/{arch}/native/libopus.dll",
        f"runtimes/{arch}/native/opus.dll",
        "runtimes/win-x64/native/libopus.dll",
        "runtimes/win-x64/native/opus.dll",
        "runtimes/win-x86/native/libopus.dll",
        "runtimes/win-x86/native/opus.dll",
    ]
    for item in preferred:
        if item in names:
            return item
    for item in names:
        lower = item.lower()
        if lower.endswith("/libopus.dll") or lower.endswith("/opus.dll"):
            return item
    return None


def install_windows_libopus(root: Path, py: str) -> bool:
    dest_dir = native_dir(root)
    dest_dir.mkdir(parents=True, exist_ok=True)
    primary = dest_dir / "libopus.dll"
    if primary.is_file() and primary.stat().st_size > 1024:
        _mirror_windows_dll(primary, py)
        return True

    arch = windows_runtime_arch()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            nupkg = Path(tmp) / "VoiceNext.Natives.nupkg"
            print("[setup] Downloading libopus for Windows...")
            urllib.request.urlretrieve(NUGET_OPUS_URL, nupkg)
            with zipfile.ZipFile(nupkg) as zf:
                member = _pick_nupkg_dll(zf.namelist(), arch)
                if not member:
                    print("[setup] libopus.dll not found in NuGet package")
                    return False
                zf.extract(member, tmp)
                src = Path(tmp) / member
                shutil.copy2(src, primary)
                opus_alias = dest_dir / "opus.dll"
                if not opus_alias.exists():
                    shutil.copy2(primary, opus_alias)
            print(f"[setup] Installed {primary}")
            _mirror_windows_dll(primary, py)
            return True
    except Exception as exc:
        print(f"[setup] Failed to download libopus: {exc}")
        return False


def _mirror_windows_dll(primary: Path, py: str) -> None:
    targets: list[Path] = []
    venv = os.environ.get("VIRTUAL_ENV", "").strip()
    if not venv:
        maybe = Path(py).resolve().parent.parent
        if (maybe / "Scripts").is_dir():
            venv = str(maybe)
    if venv:
        scripts = Path(venv) / "Scripts"
        if scripts.is_dir():
            targets.extend(scripts / name for name in WINDOWS_DLL_NAMES)
    root = repo_root()
    targets.extend(root / name for name in WINDOWS_DLL_NAMES)
    for target in targets:
        try:
            if target.resolve() == primary.resolve():
                continue
            shutil.copy2(primary, target)
        except OSError:
            pass


def _brew_prefix(formula: str) -> Path | None:
    if not shutil.which("brew"):
        return None
    try:
        proc = subprocess.run(
            ["brew", "--prefix", formula],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0:
            return None
        prefix = (proc.stdout or "").strip()
        return Path(prefix) if prefix else None
    except Exception:
        return None


def _find_brew_opus_dylib() -> Path | None:
    prefix = _brew_prefix("opus")
    if not prefix:
        return None
    lib_dir = prefix / "lib"
    for name in MACOS_DYLIB_NAMES:
        path = lib_dir / name
        if path.is_file() and path.stat().st_size > 1024:
            return path
    return None


def _bundle_macos_dylib(root: Path, src: Path) -> bool:
    dest_dir = native_dir(root)
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied = False
    for name in MACOS_DYLIB_NAMES:
        dest = dest_dir / name
        try:
            shutil.copy2(src, dest)
            copied = True
        except OSError:
            continue
    if copied:
        print(f"[setup] Installed libopus -> {dest_dir}")
    return copied


def install_macos_libopus(root: Path) -> bool:
    dest_dir = native_dir(root)
    for name in MACOS_DYLIB_NAMES:
        bundled = dest_dir / name
        if bundled.is_file() and bundled.stat().st_size > 1024:
            return True

    if shutil.which("brew"):
        if subprocess.run(["brew", "list", "opus"], capture_output=True).returncode != 0:
            print("[setup] Installing libopus via Homebrew (opus portaudio)...")
            subprocess.run(["brew", "install", "opus", "portaudio"], check=False)
        src = _find_brew_opus_dylib()
        if src and _bundle_macos_dylib(root, src):
            return True

    for lib_dir in (Path("/opt/homebrew/lib"), Path("/usr/local/lib")):
        for name in MACOS_DYLIB_NAMES:
            path = lib_dir / name
            if path.is_file() and _bundle_macos_dylib(root, path):
                return True
    return False


def linux_libopus_hint() -> None:
    if shutil.which("apt-get"):
        print("  Ubuntu/Debian: sudo apt install libopus0 portaudio19-dev python3-pyaudio")
    elif shutil.which("pacman"):
        print("  Arch: sudo pacman -S opus portaudio python-pyaudio")
    elif sys.platform == "darwin":
        print("  macOS: brew install opus portaudio  (or install Homebrew from https://brew.sh)")


def main() -> int:
    root = repo_root()
    py = sys.executable
    os.environ.setdefault("CHATXZ_ROOT", str(root))
    os.environ.setdefault("PYTHONPATH", str(root))

    if voice_ready(py, root):
        return 0

    print("[setup] Installing voice dependencies (pyaudio + libopus)...")

    if not pip_install_pyaudio(py):
        log = root / ".voice-install.log"
        print("[setup] PyAudio install failed.")
        if log.is_file():
            tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()
            for line in tail[-3:]:
                print(f"  {line}")

    if sys.platform == "win32":
        install_windows_libopus(root, py)
    elif sys.platform == "darwin":
        install_macos_libopus(root)

    if voice_ready(py, root):
        marker = Path(os.environ.get("VIRTUAL_ENV", root / ".venv")) / ".voice-ready"
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("ok\n", encoding="utf-8")
        except OSError:
            pass
        print("[setup] Native voice ready.")
        return 0

    print("[setup] Native call audio still unavailable; browser Opus works at http://127.0.0.1:8742")
    if sys.platform == "win32":
        print("  Windows: re-run run.bat web (auto-downloads libopus) or place libopus.dll in the repo folder.")
    elif sys.platform == "darwin":
        print("  macOS: install Homebrew then re-run ./run.sh web — it runs: brew install opus portaudio")
        print("  Or open http://localhost:8742 for browser microphone.")
    else:
        linux_libopus_hint()
        print("  Then re-run: ./run.sh web")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())