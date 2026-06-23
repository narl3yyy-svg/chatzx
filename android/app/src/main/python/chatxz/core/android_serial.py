"""Android USB serial support for RNS SerialInterface via usbserial4a."""

_patched = False


def ensure_android_serial_patch():
    """Route pyserial.Serial through usbserial4a for Android USB device paths."""
    global _patched
    if _patched:
        return True
    try:
        from chatxz.utils.platform import is_android
        if not is_android():
            return False
    except Exception:
        return False
    try:
        from chatxz.android_usb.bootstrap import bootstrap as bootstrap_android_usb
        bootstrap_android_usb()
    except Exception as exc:
        print(f"[serial] Android USB bootstrap failed: {exc}")
        return False
    try:
        import serial
        original = serial.Serial

        def android_serial_factory(*args, **kwargs):
            port = kwargs.get("port")
            if port is None and args:
                port = args[0]
            path = str(port or "").strip()
            if path.startswith("/dev/bus/usb"):
                from usbserial4a import serial4a
                baudrate = int(kwargs.get("baudrate") or (args[1] if len(args) > 1 else 57600))
                conn = serial4a.get_serial_port(path, baudrate=baudrate)
                if not getattr(conn, "is_open", False):
                    try:
                        conn.open()
                    except Exception:
                        pass
                return conn
            return original(*args, **kwargs)

        serial.Serial = android_serial_factory
        _patched = True
        print("[serial] Android pyserial patched for USB serial paths")
        return True
    except Exception as exc:
        print(f"[serial] Android serial patch failed: {exc}")
        return False