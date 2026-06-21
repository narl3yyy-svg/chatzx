"""System metrics helpers for the web UI status bar."""

import os
import json
import time
import subprocess


def _read_sysfs_millicelsius(path):
    try:
        with open(path) as f:
            raw = f.read().strip()
        return int(raw) / 1000.0 if raw else None
    except OSError:
        return None


def _collect_hwmon_temps():
    readings = []
    hwmon_root = "/sys/class/hwmon"
    if not os.path.isdir(hwmon_root):
        return readings

    for name in sorted(os.listdir(hwmon_root)):
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
        for name in os.listdir(base):
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


def get_avg_cpu_temperature():
    """Return average CPU temperature in °C across detected cores, or None."""
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