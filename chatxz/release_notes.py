"""Release notes shown on first install and after updates."""

from chatxz._version import __version__ as CURRENT_VERSION

RELEASE_NOTES = {
    "0.3.141": [
        "Settings → Network shows a clear warning when TCP LAN is unavailable or limited because hub mode is on.",
        "Hub server blocks switching to TCP LAN (port 4242 is reserved for group relay); hub client explains TCP LAN is for local P2P only.",
    ],
    "0.3.140": [
        "Hub client + TCP LAN: switching Primary LAN transport to TCP LAN now works while staying a hub client — P2P peers connect over TCP, group chat stays on the hub link.",
        "Hub relay no longer treats TCP LAN peer links as hub clients (fixes accidental group-chat leak over LAN TCP).",
    ],
    "0.3.139": [
        "Hub group chat is isolated to TCP hub transport — P2P-only peers (hub off) no longer receive group messages relayed from the hub server.",
        "Group messages are dropped on receive when hub mode is off, and hub server relay targets only TCP-connected hub clients.",
        "Removed unused config/config.ini template and empty chatxz/ui package.",
    ],
    "0.3.138": [
        "Changing Settings → Network IPv4 now drops all active links and clears cached RNS paths — messages no longer cross subnets after a NIC change.",
        "Beacon discovery strictly rejects peers outside your LAN /24 (no more 10.0.30.x on 10.10.100.x).",
        "RNS broadcast address follows your pinned IPv4 instead of falling back to 255.255.255.255.",
        "Ctrl+C shuts down cleanly on Linux; Ctrl+Z suspend is disabled in run.sh to avoid stuck ports.",
    ],
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