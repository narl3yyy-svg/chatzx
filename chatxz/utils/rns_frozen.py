"""PyInstaller/frozen RNS import fix.

RNS.Interfaces builds __all__ via glob(\"*.py\"), which returns nothing in a frozen
bundle. Reticulum.py then does ``from RNS.Interfaces import *`` and gets no names,
so ``Interface.Interface.MODE_FULL`` raises NameError.
"""

import importlib
import sys

_INTERFACE_MODULES = (
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


def ensure_rns_interfaces():
    """Load RNS interface modules and patch Reticulum's module namespace if needed."""
    try:
        import RNS.Interfaces as rns_ifaces
    except ImportError:
        return

    for name in _INTERFACE_MODULES:
        try:
            importlib.import_module(f"RNS.Interfaces.{name}")
        except Exception:
            pass

    if not getattr(rns_ifaces, "__all__", None) or "Interface" not in rns_ifaces.__all__:
        rns_ifaces.__all__ = list(_INTERFACE_MODULES)

    ret_mod = sys.modules.get("RNS.Reticulum")
    if ret_mod is None:
        try:
            importlib.import_module("RNS.Reticulum")
            ret_mod = sys.modules.get("RNS.Reticulum")
        except Exception:
            return

    if ret_mod is None or "Interface" in ret_mod.__dict__:
        return

    for name in _INTERFACE_MODULES:
        try:
            setattr(ret_mod, name, importlib.import_module(f"RNS.Interfaces.{name}"))
        except Exception:
            pass