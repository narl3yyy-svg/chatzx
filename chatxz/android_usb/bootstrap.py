"""Install Android USB shims before Reticulum/usbserial4a import."""

import os
import sys
import types


def bootstrap():
    os.environ.setdefault("ANDROID_ROOT", "/system")
    os.environ.setdefault("ANDROID_ARGUMENT", "")
    _install_jnius_shim()
    _install_usb4a_shim()


def _install_jnius_shim():
    if "jnius" in sys.modules:
        return

    import importlib.machinery

    jnius = types.ModuleType("jnius")

    def autoclass(name):
        from java import jclass
        return jclass(name)

    jnius.autoclass = autoclass
    jnius.__spec__ = importlib.machinery.ModuleSpec("jnius", loader=None)
    sys.modules["jnius"] = jnius


def _install_usb4a_shim():
    from chatxz.android_usb import usb4a as usb4a_pkg

    sys.modules["usb4a"] = usb4a_pkg
    sys.modules["usb4a.usb"] = usb4a_pkg.usb