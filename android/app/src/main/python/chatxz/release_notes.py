"""Release notes shown on first install and after updates."""

from chatxz._version import __version__ as CURRENT_VERSION

RELEASE_NOTES = {
    "0.3.137": [
        "Network panel (sidebar 🌐) shows live discovery/link status only.",
        "All network configuration (IPv4 pin, UDP/TCP LAN, hub, serial) lives under Settings → Network.",
        "Discovery in Auto mode now scopes to your primary LAN /24 — VPN subnets like 10.0.30.x no longer appear when you are on 10.10.100.x.",
        "Cross-subnet 10.x bleed fixed: 10.0.30.x and 10.10.100.x are separate networks again.",
        "Release notes dialog on first install and after each update.",
    ],
}


def notes_for_version(version=None):
    version = (version or CURRENT_VERSION).strip()
    return RELEASE_NOTES.get(version, [])


def release_notes_payload(version=None):
    version = (version or CURRENT_VERSION).strip()
    return {
        "version": version,
        "notes": notes_for_version(version),
        "has_notes": bool(notes_for_version(version)),
    }