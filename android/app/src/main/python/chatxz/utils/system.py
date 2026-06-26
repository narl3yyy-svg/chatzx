"""System metrics helpers for the web UI status bar."""

import os
import re
import json
import time
import subprocess
import sys

from chatxz.utils.platform import is_android


def _read_sysfs_millicelsius(path):
    try:
        with open(path) as f:
            raw = f.read().strip()
        return int(raw) / 1000.0 if raw else None
    except OSError:
        return None


def _collect_hwmon_temps():
    readings = []
    if is_android():
        return readings
    hwmon_root = "/sys/class/hwmon"
    if not os.path.isdir(hwmon_root):
        return readings

    try:
        names = sorted(os.listdir(hwmon_root))
    except (OSError, PermissionError):
        return readings

    for name in names:
        hpath = os.path.join(hwmon_root, name)
        if not os.path.isdir(hpath):
            continue
        chip = name
        name_path = os.path.join(hpath, "name")
        if os.path.exists(name_path):
            with open(name_path) as f:
                chip = f.read().strip().lower()

        for entry in sorted(os.listdir(hpath)):
            if not entry.endswith("_input") or "temp" not in entry:
                continue
            celsius = _read_sysfs_millicelsius(os.path.join(hpath, entry))
            if celsius is None:
                continue
            label = entry.replace("_input", "")
            label_path = os.path.join(hpath, entry.replace("_input", "_label"))
            if os.path.exists(label_path):
                with open(label_path) as f:
                    label = f.read().strip().lower()
            readings.append({
                "celsius": celsius,
                "chip": chip,
                "label": label,
                "is_core": "core" in label or chip == "coretemp",
                "is_cpu": chip in ("coretemp", "k10temp", "zenpower", "cpu_thermal")
                    or "cpu" in chip or "x86_pkg_temp" in label or "tctl" in label,
            })
    return readings


def _collect_thermal_zone_temps():
    readings = []
    for base in ("/sys/class/thermal", "/sys/devices/virtual/thermal"):
        if not os.path.isdir(base):
            continue
        try:
            zone_names = os.listdir(base)
        except (OSError, PermissionError):
            continue
        for name in zone_names:
            if not name.startswith("thermal_zone"):
                continue
            tpath = os.path.join(base, name, "temp")
            celsius = _read_sysfs_millicelsius(tpath)
            if celsius is None:
                continue
            ttype = "unknown"
            ttype_path = os.path.join(base, name, "type")
            if os.path.exists(ttype_path):
                with open(ttype_path) as f:
                    ttype = f.read().strip().lower()
            readings.append({
                "celsius": celsius,
                "chip": ttype,
                "label": ttype,
                "is_core": "cpu" in ttype or "x86" in ttype,
                "is_cpu": "cpu" in ttype or "x86" in ttype,
            })
    return readings


def _collect_sensors_temps():
    readings = []
    try:
        result = subprocess.run(["sensors", "-j"], capture_output=True, text=True, timeout=3)
        if result.returncode != 0 or not result.stdout.strip():
            return readings
        data = json.loads(result.stdout)
        for chip, vals in data.items():
            chip_l = chip.lower()
            if not isinstance(vals, dict):
                continue
            for key, val in vals.items():
                if not isinstance(val, dict):
                    continue
                key_l = key.lower()
                for sk, sv in val.items():
                    if sk.endswith("_input") and isinstance(sv, (int, float)):
                        readings.append({
                            "celsius": float(sv),
                            "chip": chip_l,
                            "label": key_l,
                            "is_core": "core" in key_l,
                            "is_cpu": "core" in key_l or "cpu" in chip_l or "tctl" in key_l,
                        })
    except Exception:
        pass
    return readings


def _windows_cpu_percent():
    try:
        ps = (
            "(Get-Counter '\\Processor(_Total)\\% Processor Time' -ErrorAction Stop)"
            ".CounterSamples[0].CookedValue"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode == 0 and result.stdout.strip():
            return round(float(result.stdout.strip()), 1)
    except Exception:
        pass
    try:
        import ctypes

        class FILETIME(ctypes.Structure):
            _fields_ = [
                ("dwLowDateTime", ctypes.c_uint32),
                ("dwHighDateTime", ctypes.c_uint32),
            ]

        def _ft_to_int(ft):
            return (ft.dwHighDateTime << 32) + ft.dwLowDateTime

        idle1, kernel1, user1 = FILETIME(), FILETIME(), FILETIME()
        idle2, kernel2, user2 = FILETIME(), FILETIME(), FILETIME()
        ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle1), ctypes.byref(kernel1), ctypes.byref(user1),
        )
        time.sleep(0.25)
        ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle2), ctypes.byref(kernel2), ctypes.byref(user2),
        )
        idle = _ft_to_int(idle2) - _ft_to_int(idle1)
        total = (
            (_ft_to_int(kernel2) - _ft_to_int(kernel1))
            + (_ft_to_int(user2) - _ft_to_int(user1))
            + idle
        )
        if total > 0:
            return round(100.0 * (1.0 - idle / total), 1)
    except Exception:
        pass
    return None


def _windows_cpu_temperature():
    readings = []
    try:
        ps = (
            "Get-CimInstance -Namespace root/WMI -ClassName MSAcpi_ThermalZoneTemperature "
            "| ForEach-Object { [math]::Round(($_.CurrentTemperature - 2732) / 10.0, 1) }"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    c = float(line)
                    if 0 < c < 120:
                        readings.append(c)
                except ValueError:
                    pass
    except Exception:
        pass
    if readings:
        return round(sum(readings) / len(readings), 1)
    return None


def _darwin_cpu_percent():
    try:
        result = subprocess.run(
            ["top", "-l", "1", "-n", "0", "-s", "0"],
            capture_output=True, text=True, timeout=4,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if "CPU usage" not in line:
                    continue
                m = re.search(
                    r"(\d+(?:\.\d+)?)%\s+user.*?(\d+(?:\.\d+)?)%\s+sys",
                    line,
                )
                if m:
                    return round(float(m.group(1)) + float(m.group(2)), 1)
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["ps", "-A", "-o", "%cpu"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            total = 0.0
            for line in result.stdout.splitlines()[1:]:
                try:
                    total += float(line.strip())
                except ValueError:
                    pass
            cores = os.cpu_count() or 1
            return min(round(total / cores, 1), 100.0)
    except Exception:
        pass
    return None


def _parse_powermetrics_temps(text):
    readings = []
    for line in (text or "").splitlines():
        m = re.search(r"CPU die temperature:\s*([\d.]+)\s*C", line, re.I)
        if m:
            readings.append(float(m.group(1)))
        m = re.search(r"GPU die temperature:\s*([\d.]+)\s*C", line, re.I)
        if m:
            readings.append(float(m.group(1)))
        m = re.search(r"ANE die temperature:\s*([\d.]+)\s*C", line, re.I)
        if m:
            readings.append(float(m.group(1)))
    return readings


def _run_powermetrics_smc():
    cmds = [
        ["powermetrics", "--samplers", "smc", "-n", "1", "-i", "100"],
        ["sudo", "-n", "powermetrics", "--samplers", "smc", "-n", "1", "-i", "100"],
        ["powermetrics", "--samplers", "thermal", "-n", "1", "-i", "100"],
    ]
    for cmd in cmds:
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                readings = _parse_powermetrics_temps(result.stdout)
                if readings:
                    return readings
        except Exception:
            pass
    return []


def _darwin_ioreg_temps():
    readings = []
    try:
        result = subprocess.run(
            ["ioreg", "-l", "-w", "0"],
            capture_output=True, text=True, timeout=8,
        )
        if result.returncode != 0:
            return readings
        text = result.stdout
        patterns = [
            r"CPU\s+die\s+temperature[^0-9]*([\d.]+)",
            r"GPU\s+die\s+temperature[^0-9]*([\d.]+)",
            r"thermal-temperature[^0-9]*([\d.]+)",
            r'"temperature"\s*=\s*([\d.]+)',
        ]
        for pattern in patterns:
            for m in re.finditer(pattern, text, re.I):
                try:
                    t = float(m.group(1))
                except ValueError:
                    continue
                if t > 200:
                    t /= 1000.0
                if 20 < t < 120:
                    readings.append(t)
    except Exception:
        pass
    return readings


def _darwin_thermal_pressure_estimate():
    try:
        result = subprocess.run(
            ["sysctl", "-n", "machdep.xcpm.cpu_thermal_level"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0 and result.stdout.strip().isdigit():
            level = int(result.stdout.strip())
            if level > 0:
                return round(35.0 + min(level, 50) * 0.8, 1)
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["pmset", "-g", "therm"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if "CPU_Speed_Limit" not in line:
                    continue
                m = re.search(r"CPU_Speed_Limit\s*=\s*(\d+)", line)
                if not m:
                    continue
                limit = int(m.group(1))
                if limit < 100:
                    return round(40.0 + (100 - limit) * 0.45, 1)
    except Exception:
        pass
    return None


def _darwin_cpu_temperature():
    """Return (celsius, approx) for macOS."""
    readings = _run_powermetrics_smc()
    if not readings:
        readings = _darwin_ioreg_temps()
    if readings:
        return round(sum(readings) / len(readings), 1), False
    estimate = _darwin_thermal_pressure_estimate()
    if estimate is not None:
        return estimate, True
    return None, False


def get_cpu_temperature_detail():
    """Return {avg_celsius, approx} for the status bar."""
    if sys.platform == "win32":
        temp = _windows_cpu_temperature()
        return {"avg_celsius": temp, "approx": False}
    if sys.platform == "darwin" and not is_android():
        temp, approx = _darwin_cpu_temperature()
        return {"avg_celsius": temp, "approx": approx}
    temp = get_avg_cpu_temperature()
    return {"avg_celsius": temp, "approx": False}


def get_avg_cpu_temperature():
    """Return average CPU temperature in °C across detected cores, or None."""
    if sys.platform == "win32":
        return _windows_cpu_temperature()
    if sys.platform == "darwin" and not is_android():
        temp, _approx = _darwin_cpu_temperature()
        return temp

    if is_android():
        readings = _collect_thermal_zone_temps()
    else:
        readings = _collect_hwmon_temps()
    if not readings:
        readings = _collect_thermal_zone_temps()
    if not readings:
        readings = _collect_sensors_temps()

    core_temps = [r["celsius"] for r in readings if r.get("is_core")]
    if not core_temps:
        core_temps = [r["celsius"] for r in readings if r.get("is_cpu")]
    if not core_temps and readings:
        core_temps = [r["celsius"] for r in readings]

    if not core_temps:
        return None
    return round(sum(core_temps) / len(core_temps), 1)


def get_cpu_percent():
    """Return CPU usage percent, or None on failure."""
    if sys.platform == "win32":
        return _windows_cpu_percent()
    if sys.platform == "darwin" and not is_android():
        return _darwin_cpu_percent()

    try:
        with open("/proc/stat") as f:
            parts = [int(x) for x in f.readline().split()[1:]]
        total1, idle1 = sum(parts), parts[3]
        time.sleep(0.25)
        with open("/proc/stat") as f:
            parts = [int(x) for x in f.readline().split()[1:]]
        total2, idle2 = sum(parts), parts[3]
        dt, di = total2 - total1, idle2 - idle1
        if dt > 0:
            return round(100.0 * (1.0 - di / dt), 1)
    except Exception:
        pass
    try:
        with open("/proc/loadavg") as f:
            la = float(f.read().split()[0])
        nproc = os.cpu_count() or 1
        return min(round(la / nproc * 100, 1), 100.0)
    except Exception:
        return None