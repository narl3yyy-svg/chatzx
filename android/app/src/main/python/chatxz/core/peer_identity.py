"""Canonical peer connect hashes — one message-destination hash per machine."""

import base64

import RNS

from chatxz.core.discovery import APP_NAME, message_dest_hash_for_identity, normalize_hash

PUBKEY_SIZE = RNS.Identity.KEYSIZE // 8


def identity_hex_from_object(ident):
    if not ident or not getattr(ident, "hash", None):
        return ""
    return normalize_hash(RNS.hexrep(ident.hash))


def connect_hash_from_identity(ident):
    """Message destination hash peers use to connect (not raw identity hash)."""
    return message_dest_hash_for_identity(ident)


def connect_hash_for_manager(identity_mgr, destination=None):
    """Best-effort connect hash before or after RNS messaging is up."""
    if destination and getattr(destination, "hash", None):
        return normalize_hash(RNS.hexrep(destination.hash))
    if identity_mgr and identity_mgr.identity:
        computed = connect_hash_from_identity(identity_mgr.identity)
        if computed:
            return computed
    return ""


def register_beacon_identity(data):
    """Register peer from LAN beacon; return canonical connect hash or ""."""
    if not data:
        return ""
    pubkey_b64 = data.get("pubkey")
    if not pubkey_b64:
        return ""
    try:
        pubkey = base64.b64decode(pubkey_b64, validate=True)
    except Exception:
        return ""
    if len(pubkey) != PUBKEY_SIZE:
        return ""

    dest_hex = normalize_hash(data.get("hash"))
    identity_hex = normalize_hash(data.get("identity_hash"))
    if not identity_hex or len(identity_hex) != 32:
        return ""

    try:
        identity_bytes = bytes.fromhex(identity_hex)
    except ValueError:
        return ""

    app_data = None
    name = (data.get("name") or "").strip()
    if name:
        try:
            app_data = {"app": APP_NAME, "name": name}
            app_data = __import__("json").dumps(app_data).encode("utf-8")
        except Exception:
            app_data = None

    computed_dest = ""
    try:
        ident_probe = RNS.Identity.recall(identity_bytes)
        if ident_probe:
            computed_dest = connect_hash_from_identity(ident_probe)
    except Exception:
        pass

    dest_bytes = None
    canonical_dest = computed_dest or (dest_hex if len(dest_hex) == 32 else "")
    if canonical_dest:
        try:
            dest_bytes = bytes.fromhex(canonical_dest)
        except ValueError:
            dest_bytes = None
    if dest_bytes is None and dest_hex and len(dest_hex) == 32:
        try:
            dest_bytes = bytes.fromhex(dest_hex)
        except ValueError:
            dest_bytes = None

    purge_stale_known_destinations(pubkey, canonical_dest or dest_hex, identity_bytes)

    try:
        RNS.Identity.remember(identity_bytes, dest_bytes, pubkey, app_data)
    except Exception:
        return ""

    ident = None
    try:
        ident = RNS.Identity.recall(identity_bytes)
    except Exception:
        pass
    canonical = connect_hash_from_identity(ident) if ident else ""
    if not canonical and len(dest_hex) == 32:
        canonical = dest_hex
    if not canonical:
        return ""

    if canonical != dest_hex:
        try:
            RNS.Identity.remember(
                identity_bytes, bytes.fromhex(canonical), pubkey, app_data,
            )
        except Exception:
            pass
    return canonical


def register_identity_from_announce(peer, announced_identity=None):
    """Register peer identity as soon as an RNS announce is received."""
    if not peer:
        return ""
    identity_hex = normalize_hash(peer.get("identity_hash") or "")
    if not identity_hex and announced_identity and getattr(announced_identity, "hash", None):
        identity_hex = normalize_hash(RNS.hexrep(announced_identity.hash))
    pubkey_b64 = (peer.get("pubkey") or "").strip()
    if not pubkey_b64 and announced_identity:
        try:
            pubkey_b64 = base64.b64encode(
                announced_identity.get_public_key()
            ).decode("ascii")
        except Exception:
            pubkey_b64 = ""
    if not identity_hex or not pubkey_b64:
        return ""
    payload = dict(peer)
    payload["identity_hash"] = identity_hex
    payload["pubkey"] = pubkey_b64
    return register_beacon_identity(payload) or ""


def peer_record_from_beacon(data):
    """Build a normalized discovery peer dict from beacon payload."""
    if not data or data.get("app") != APP_NAME:
        return None
    connect = ""
    try:
        connect = register_beacon_identity(data) or ""
    except Exception:
        connect = ""
    hash_hex = normalize_hash(connect or data.get("hash"))
    if len(hash_hex) != 32:
        return None
    identity_hex = normalize_hash(data.get("identity_hash"))
    name = (data.get("name") or "").strip() or hash_hex[:8]
    peer = {
        "hash": hash_hex,
        "name": name,
        "app": APP_NAME,
        "ip": (data.get("ip") or "").strip() or None,
        "port": data.get("port", 8742),
        "via": "beacon",
    }
    if identity_hex and identity_hex != hash_hex:
        peer["identity_hash"] = identity_hex
    if data.get("pubkey"):
        peer["pubkey"] = data.get("pubkey")
    return peer


def purge_stale_known_destinations(pubkey, canonical_dest, identity_bytes=None):
    """Drop RNS known_destinations entries for a pubkey that use a wrong dest hash."""
    if not pubkey:
        return 0
    canonical = normalize_hash(canonical_dest)
    removed = 0
    try:
        with RNS.Identity.known_destinations_lock:
            stale = []
            for dest_hash_bytes, entry in RNS.Identity.known_destinations.items():
                if len(entry) < 3 or entry[2] != pubkey:
                    continue
                found = normalize_hash(RNS.hexrep(dest_hash_bytes))
                if canonical and found != canonical:
                    stale.append(dest_hash_bytes)
            for raw in stale:
                try:
                    del RNS.Identity.known_destinations[raw]
                    removed += 1
                except Exception:
                    pass
    except Exception:
        pass
    return removed


def purge_rns_paths_for_hashes(hashes):
    """Drop stale RNS path entries when a peer identity is superseded."""
    try:
        from chatxz.core.lan_rns import scrub_peer_path
    except Exception:
        scrub_peer_path = None
    removed = 0
    targets = {normalize_hash(h) for h in (hashes or []) if normalize_hash(h)}
    if not targets:
        return 0
    for clean in targets:
        if scrub_peer_path:
            try:
                scrub_peer_path(clean)
            except Exception:
                pass
        try:
            raw = bytes.fromhex(clean)
            if hasattr(RNS.Transport, "clear_path"):
                RNS.Transport.clear_path(raw)
                removed += 1
        except Exception:
            pass
    return removed