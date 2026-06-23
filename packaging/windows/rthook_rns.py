"""PyInstaller runtime hook — RNS.Interfaces uses glob() for __all__, which breaks when frozen."""

import importlib

_RNS_INTERFACE_MODULES = (
    "Interface",
    "UDPInterface",
    "AutoInterface",
    "TCPInterface",
    "LocalInterface",
    "SerialInterface",
    "BackboneInterface",
    "KISSInterface",
    "PipeInterface",
    "I2PInterface",
    "RNodeInterface",
    "RNodeMultiInterface",
    "WeaveInterface",
    "AX25KISSInterface",
)

_RNS_CORE_MODULES = (
    "RNS",
    "RNS.Reticulum",
    "RNS.Transport",
    "RNS.Destination",
    "RNS.Link",
    "RNS.Packet",
    "RNS.Resource",
    "RNS.Identity",
    "RNS.Cryptography",
    "RNS.vendor.configobj",
    "RNS.vendor.platformutils",
)

for _mod in _RNS_CORE_MODULES:
    try:
        importlib.import_module(_mod)
    except Exception:
        pass

import RNS.Interfaces as _rns_ifaces

for _name in _RNS_INTERFACE_MODULES:
    try:
        importlib.import_module(f"RNS.Interfaces.{_name}")
    except Exception:
        pass

# glob() in RNS/Interfaces/__init__.py returns nothing in a frozen bundle.
if not getattr(_rns_ifaces, "__all__", None) or "Interface" not in _rns_ifaces.__all__:
    _rns_ifaces.__all__ = list(_RNS_INTERFACE_MODULES)