"""Native folder picker helpers (Windows portable exe has no tkinter)."""

import os
import sys


def pick_folder(start_dir=None):
    """Return an absolute folder path, or None if cancelled / unavailable."""
    start = os.path.expanduser(start_dir or os.path.expanduser("~"))
    if not os.path.isdir(start):
        start = os.path.expanduser("~")

    if sys.platform == "win32":
        for picker in (_pick_folder_windows_ctypes, _pick_folder_windows_powershell):
            picked = picker(start)
            if picked:
                return os.path.normpath(picked)
        return None
    if sys.platform == "darwin":
        return _pick_folder_macos(start)
    return None


def _pick_folder_windows_ctypes(start_dir):
    """SHBrowseForFolder — works in PyInstaller console apps (visible dialog)."""
    try:
        import ctypes
        import ctypes.wintypes as wintypes

        shell32 = ctypes.windll.shell32
        ole32 = ctypes.windll.ole32

        BIF_RETURNONLYFSDIRS = 0x0001
        BIF_NEWDIALOGSTYLE = 0x0040

        class BROWSEINFO(ctypes.Structure):
            _fields_ = [
                ("hwndOwner", wintypes.HWND),
                ("pidlRoot", ctypes.c_void_p),
                ("pszDisplayName", wintypes.LPWSTR),
                ("lpszTitle", wintypes.LPWSTR),
                ("ulFlags", wintypes.UINT),
                ("lpfn", ctypes.c_void_p),
                ("lParam", wintypes.LPARAM),
                ("iImage", ctypes.c_int),
            ]

        display = ctypes.create_unicode_buffer(260)
        bi = BROWSEINFO()
        bi.hwndOwner = 0
        bi.pszDisplayName = display
        bi.lpszTitle = "Select received files folder"
        bi.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE

        pidl = shell32.SHBrowseForFolderW(ctypes.byref(bi))
        if not pidl:
            return None
        path_buf = ctypes.create_unicode_buffer(260)
        try:
            if shell32.SHGetPathFromIDListW(pidl, path_buf):
                return path_buf.value or None
        finally:
            ole32.CoTaskMemFree(pidl)
    except Exception:
        pass
    return None


def _pick_folder_windows_powershell(start_dir):
    """Fallback — must NOT use CREATE_NO_WINDOW or the dialog stays hidden."""
    import subprocess

    start_esc = start_dir.replace("'", "''")
    script = f"""
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Select received files folder'
$dialog.ShowNewFolderButton = $true
if (Test-Path -LiteralPath '{start_esc}') {{
    $dialog.SelectedPath = '{start_esc}'
}}
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {{
    Write-Output $dialog.SelectedPath
}}
"""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", script],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        picked = (proc.stdout or "").strip()
        if picked:
            return os.path.normpath(picked)
    except Exception:
        pass
    return None


def _pick_folder_macos(start_dir):
    import subprocess

    start_posix = start_dir.replace("\\", "/")
    script = (
        'POSIX path of (choose folder with prompt "Select received files folder" '
        f'default location POSIX file "{start_posix}")'
    )
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        picked = (proc.stdout or "").strip()
        if picked and picked != "/":
            return os.path.normpath(picked)
    except Exception:
        pass
    return None