# PyInstaller spec — macOS .app bundle (chatxz.app)
# Build: bash packaging/macos/build-portable.sh

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]

block_cipher = None

hiddenimports = []
for pkg in ("RNS", "aiohttp", "cryptography", "multidict", "yarl", "frozenlist", "aiosignal", "attrs", "configobj"):
    try:
        hiddenimports.extend(collect_submodules(pkg))
    except Exception:
        pass

hiddenimports += [
    "RNS.Interfaces.Interface",
    "RNS.Interfaces.UDPInterface",
    "RNS.Interfaces.AutoInterface",
    "RNS.Interfaces.TCPInterface",
    "RNS.Interfaces.LocalInterface",
    "RNS.Interfaces.SerialInterface",
    "RNS.Interfaces.BackboneInterface",
    "RNS.vendor.configobj",
    "RNS.vendor.platformutils",
]

datas = []
datas += collect_data_files("chatxz", includes=["**/*.html", "**/*.css", "**/*.js", "**/*.json", "**/*.svg", "**/*.png"])
datas.append((str(ROOT / "chatxz" / "web" / "static"), "chatxz/web/static"))
datas.append((str(ROOT / "packaging" / "macos" / "README-PORTABLE.txt"), "."))

a = Analysis(
    [str(ROOT / "chatxz" / "portable.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports + [
        "chatxz",
        "chatxz.web.server",
        "chatxz.core.messaging",
        "chatxz.core.discovery",
        "chatxz.core.lan_beacon",
        "chatxz.core.lan_rns",
        "chatxz.core.rns_interfaces",
        "chatxz.core.identity",
        "chatxz.core.contacts",
        "chatxz.core.voice",
        "chatxz.utils.helpers",
        "chatxz.utils.platform",
        "chatxz.utils.folder_picker",
        "chatxz.utils.rns_frozen",
        "chatxz.utils.system",
        "chatxz._version",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(ROOT / "packaging" / "macos" / "rthook_rns.py")],
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "PIL", "pytest"],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="chatxz",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="chatxz",
)

app = BUNDLE(
    coll,
    name="chatxz.app",
    icon=None,
    bundle_identifier="com.chatxz.macos",
    info_plist={
        "CFBundleName": "chatxz",
        "CFBundleDisplayName": "chatxz",
        "CFBundleVersion": "0.3.70",
        "CFBundleShortVersionString": "0.3.70",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
    },
)
