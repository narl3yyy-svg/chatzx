"""Saved contact storage (JSON per peer hash)."""

import json
import os


def contacts_dir(config_dir):
    path = os.path.join(config_dir, "contacts")
    os.makedirs(path, exist_ok=True)
    return path


def _contact_path(config_dir, peer_hash):
    clean = (peer_hash or "").strip().replace(":", "")
    return os.path.join(contacts_dir(config_dir), clean)


def normalize_contact(entry):
    """Ensure dual-hash fields exist (v0.5 migration)."""
    if not entry:
        return entry
    h = (entry.get("hash") or "").replace(":", "")
    lan = (entry.get("lan_hash") or "").replace(":", "")
    serial = (entry.get("serial_hash") or "").replace(":", "")
    if serial and lan and lan == serial:
        if (entry.get("ip") or "").strip():
            entry.pop("serial_hash", None)
            serial = ""
        else:
            entry.pop("lan_hash", None)
            lan = ""
            if h == serial:
                entry.pop("hash", None)
                h = ""
    if serial and h == serial and not lan:
        entry.pop("hash", None)
        h = ""
    if h and not lan and not serial:
        entry["lan_hash"] = h
        lan = h
    if lan and not entry.get("hash"):
        entry["hash"] = lan
    if lan and not entry.get("identity_hash") and entry.get("lan_identity_hash"):
        entry["identity_hash"] = entry["lan_identity_hash"]
    if entry.get("custom_name") is None:
        entry["custom_name"] = False
    return entry


def _name_is_hash_like(name, hashes):
    """True when a display name is just a hash prefix, not a user label."""
    raw = (name or "").strip().lower()
    if not raw:
        return True
    for h in hashes:
        if not h:
            continue
        if raw == h or raw == h[:8] or raw == h[:12] or raw.startswith(h[:8]):
            return True
    return False


def _resolve_contact_name(existing, name=None, custom_name=False):
    """Pick stored contact name; user-chosen names always win over discovery."""
    entry = normalize_contact(existing or {})
    hashes = _contact_hashes(entry)
    incoming = str(name).strip() if name is not None and str(name).strip() else ""
    if custom_name and incoming:
        return incoming
    if entry.get("custom_name"):
        return (entry.get("name") or "").strip() or incoming
    if incoming and not _name_is_hash_like(incoming, hashes):
        return incoming
    saved = (entry.get("name") or "").strip()
    if saved and not _name_is_hash_like(saved, hashes):
        return saved
    return incoming or saved


def contact_primary_hash(contact):
    c = normalize_contact(contact or {})
    return (
        (c.get("lan_hash") or "").replace(":", "")
        or (c.get("serial_hash") or "").replace(":", "")
        or (c.get("hash") or "").replace(":", "")
    )


def _contact_hashes(contact):
    c = normalize_contact(contact or {})
    out = set()
    for key in ("hash", "lan_hash", "serial_hash", "identity_hash", "lan_identity_hash", "serial_identity_hash"):
        h = (c.get(key) or "").replace(":", "")
        if h:
            out.add(h)
    return out


def find_contact_by_hash(config_dir, peer_hash):
    """Return a saved contact matching any stored hash field."""
    clean = (peer_hash or "").strip().replace(":", "")
    if not clean:
        return None
    for contact in list_contacts(config_dir):
        if clean in _contact_hashes(contact):
            return normalize_contact(contact)
    return None


def contact_has_hash(config_dir, peer_hash):
    return find_contact_by_hash(config_dir, peer_hash) is not None


def _peer_transport_family(via):
    raw = (via or "").strip().lower()
    if raw == "serial":
        return "serial"
    return "lan"


def _names_related(a, b):
    """Loose name match for discovery refresh (e.g. 330s vs 330ss)."""
    left = (a or "").strip().lower()
    right = (b or "").strip().lower()
    if not left or not right:
        return False
    if left == right:
        return True
    return left.startswith(right) or right.startswith(left)


def _contact_matches_discovery_peer(contact, peer, peers_equivalent=None):
    c = normalize_contact(contact or {})
    peer = dict(peer or {})
    new_hash = (peer.get("hash") or "").replace(":", "")
    if not new_hash:
        return False
    if new_hash in _contact_hashes(c):
        return True
    peer_ident = (peer.get("identity_hash") or "").replace(":", "")
    if peer_ident and peer_ident in _contact_hashes(c):
        return True
    if peers_equivalent:
        lan = (c.get("lan_hash") or c.get("hash") or "").replace(":", "")
        serial = (c.get("serial_hash") or "").replace(":", "")
        c_ident = (c.get("identity_hash") or "").replace(":", "")
        if peers_equivalent(lan, new_hash) or peers_equivalent(serial, new_hash):
            return True
        if peer_ident and c_ident and peers_equivalent(c_ident, peer_ident):
            return True
    peer_ip = (peer.get("ip") or "").strip()
    c_ip = (c.get("ip") or "").strip()
    if peer_ip and c_ip and peer_ip == c_ip:
        return True
    peer_name = (peer.get("name") or "").strip()
    c_name = (c.get("name") or "").strip()
    if peer_ip and _names_related(c_name, peer_name):
        return True
    if peer_name and _names_related(c_name, peer_name):
        lan = (c.get("lan_hash") or c.get("hash") or "").replace(":", "")
        serial = (c.get("serial_hash") or "").replace(":", "")
        if lan == serial and lan:
            return True
    return False


def sync_contact_from_discovery(
    config_dir,
    peer,
    peers_equivalent=None,
    local_scope_ip=None,
):
    """Refresh saved contact transport hashes from a live discovery peer."""
    peer = dict(peer or {})
    new_hash = (peer.get("hash") or "").replace(":", "")
    if not new_hash:
        return None
    family = _peer_transport_family(peer.get("via"))
    peer_ip = (peer.get("ip") or "").strip()
    peer_ident = (peer.get("identity_hash") or "").replace(":", "")
    peer_name = peer.get("name")
    peer_port = peer.get("port")

    for contact in list_contacts(config_dir):
        if not _contact_matches_discovery_peer(contact, peer, peers_equivalent):
            continue
        entry = normalize_contact(dict(contact))
        lan = (entry.get("lan_hash") or entry.get("hash") or "").replace(":", "")
        serial = (entry.get("serial_hash") or "").replace(":", "")
        changed = False

        if family == "serial":
            if serial != new_hash:
                entry["serial_hash"] = new_hash
                changed = True
            if peer_ident:
                entry["serial_identity_hash"] = peer_ident
                changed = True
        else:
            if lan != new_hash:
                entry["lan_hash"] = new_hash
                entry["hash"] = new_hash
                changed = True
            if lan == serial and serial and new_hash != serial:
                entry["lan_hash"] = new_hash
                entry["hash"] = new_hash
                changed = True
            if peer_ip and should_update_contact_ip(
                (entry.get("ip") or "").strip(), peer_ip, local_scope_ip
            ):
                entry["ip"] = peer_ip
                changed = True
            elif peer_ip and not (entry.get("ip") or "").strip():
                entry["ip"] = peer_ip
                changed = True
            if peer_port is not None:
                try:
                    port_val = int(peer_port)
                except (TypeError, ValueError):
                    port_val = None
                if port_val is not None and entry.get("port") != port_val:
                    entry["port"] = port_val
                    changed = True
            if peer_ident:
                entry["lan_identity_hash"] = peer_ident
                entry["identity_hash"] = peer_ident
                changed = True

        if peer_name:
            resolved = _resolve_contact_name(entry, peer_name)
            if resolved and resolved != (entry.get("name") or "").strip():
                if not entry.get("custom_name"):
                    entry["name"] = resolved
                    changed = True

        if not changed:
            return entry

        file_key = contact_primary_hash(entry) or new_hash
        path = _contact_path(config_dir, file_key)
        with open(path, "w") as fh:
            json.dump(entry, fh, indent=2)
        stale = dict(contact)
        stale.update(entry)
        _purge_stale_contact_files(config_dir, stale, file_key)
        return entry
    return None


def update_contact_transport_hash(
    config_dir,
    old_hash,
    new_hash,
    via=None,
    name=None,
    ip=None,
    port=None,
    identity_hash=None,
):
    """Refresh lan_hash or serial_hash when discovery supersedes one transport row."""
    old_clean = (old_hash or "").strip().replace(":", "")
    new_clean = (new_hash or "").strip().replace(":", "")
    if not old_clean or not new_clean or old_clean == new_clean:
        return None
    contact = find_contact_by_hash(config_dir, old_clean)
    if not contact:
        return None
    entry = dict(contact)
    transport = (via or "").strip().lower()
    is_serial = transport == "serial" or (
        not transport and old_clean == (entry.get("serial_hash") or "").replace(":", "")
    )
    if is_serial:
        entry["serial_hash"] = new_clean
        if identity_hash:
            entry["serial_identity_hash"] = str(identity_hash).strip().replace(":", "")
    else:
        entry["lan_hash"] = new_clean
        entry["hash"] = new_clean
        if identity_hash:
            ident = str(identity_hash).strip().replace(":", "")
            entry["lan_identity_hash"] = ident
            entry["identity_hash"] = ident
        if ip is not None and str(ip).strip():
            entry["ip"] = str(ip).strip()
        if port is not None:
            try:
                entry["port"] = int(port)
            except (TypeError, ValueError):
                pass
    if name is not None and str(name).strip():
        entry["name"] = _resolve_contact_name(entry, name)
    file_key = contact_primary_hash(entry) or new_clean
    path = _contact_path(config_dir, file_key)
    with open(path, "w") as fh:
        json.dump(normalize_contact(entry), fh, indent=2)
    if old_clean != file_key:
        _unlink_contact_file(config_dir, old_clean)
    return normalize_contact(entry)


def load_contact(config_dir, filename):
    path = os.path.join(contacts_dir(config_dir), filename)
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as fh:
            raw = fh.read().strip()
        if not raw:
            return normalize_contact({"hash": filename, "name": filename})
        if raw.startswith("{"):
            data = json.loads(raw)
            if isinstance(data, dict):
                data.setdefault("hash", filename)
                data.setdefault("name", filename)
                return normalize_contact(data)
        return normalize_contact({"hash": filename, "name": raw})
    except Exception:
        return normalize_contact({"hash": filename, "name": filename})


def save_contact(
    config_dir,
    peer_hash,
    name=None,
    ip=None,
    port=None,
    identity_hash=None,
    via=None,
    lan_hash=None,
    serial_hash=None,
    lan_identity_hash=None,
    serial_identity_hash=None,
    custom_name=None,
):
    clean = (peer_hash or "").strip().replace(":", "")
    if not clean:
        raise ValueError("hash required")
    lan_hex = (lan_hash or "").strip().replace(":", "")
    ser_hex = (serial_hash or "").strip().replace(":", "")
    transport = (via or "").strip().lower()
    explicit_serial = transport == "serial"
    explicit_lan = transport in ("lan", "rns", "beacon", "udp", "tcp")
    dual_save = bool(lan_hex and ser_hex and lan_hex != ser_hex)
    if dual_save:
        explicit_serial = False
        explicit_lan = True
    elif ser_hex and not lan_hex and not explicit_lan:
        explicit_serial = True
    elif lan_hex and not ser_hex and not explicit_serial:
        explicit_lan = True
    is_serial = explicit_serial and not explicit_lan
    if not explicit_serial and not explicit_lan:
        is_serial = False
    display = str(name).strip() if name is not None and str(name).strip() else None
    user_named = bool(custom_name)

    merged = find_contact_by_hash(config_dir, clean)
    if merged:
        merged = dict(merged)
    elif identity_hash:
        ident = str(identity_hash).strip().replace(":", "")
        for c in list_contacts(config_dir):
            if ident in _contact_hashes(c):
                merged = dict(normalize_contact(c))
                break
    if not merged and display:
        for c in list_contacts(config_dir):
            cname = (c.get("name") or "").strip()
            if cname.lower() == display.lower() or _names_related(cname, display):
                merged = dict(normalize_contact(c))
                break

    existing = merged or load_contact(config_dir, clean) or {"hash": clean, "name": clean or display}
    existing = normalize_contact(existing)
    if user_named:
        existing["custom_name"] = True
    resolved_name = _resolve_contact_name(existing, display, custom_name=user_named)
    if resolved_name:
        existing["name"] = resolved_name
    if dual_save:
        existing["lan_hash"] = lan_hex
        existing["hash"] = lan_hex
        existing["serial_hash"] = ser_hex
        if lan_identity_hash or identity_hash:
            ident = str(lan_identity_hash or identity_hash).strip().replace(":", "")
            existing["lan_identity_hash"] = ident
            existing["identity_hash"] = ident
        if serial_identity_hash:
            existing["serial_identity_hash"] = str(
                serial_identity_hash
            ).strip().replace(":", "")
        if ip is not None and str(ip).strip():
            existing["ip"] = str(ip).strip()
        if port is not None:
            try:
                existing["port"] = int(port)
            except (TypeError, ValueError):
                pass
    elif is_serial:
        ser_target = ser_hex or (clean if clean != lan_hex else "")
        if ser_target:
            existing["serial_hash"] = ser_target
        if serial_identity_hash or identity_hash:
            existing["serial_identity_hash"] = str(
                serial_identity_hash or identity_hash
            ).strip().replace(":", "")
        lan = (existing.get("lan_hash") or "").replace(":", "")
        if lan and lan != ser_target:
            existing["hash"] = lan
        else:
            existing.pop("hash", None)
            if (existing.get("lan_hash") or "").replace(":", "") == ser_target:
                existing.pop("lan_hash", None)
    else:
        existing["lan_hash"] = lan_hex or clean
        existing["hash"] = existing["lan_hash"]
        if lan_identity_hash or identity_hash:
            ident = str(lan_identity_hash or identity_hash).strip().replace(":", "")
            existing["lan_identity_hash"] = ident
            existing["identity_hash"] = ident
        if ip is not None and str(ip).strip():
            existing["ip"] = str(ip).strip()
        if port is not None:
            try:
                existing["port"] = int(port)
            except (TypeError, ValueError):
                pass
        if ser_hex:
            existing["serial_hash"] = ser_hex
            if serial_identity_hash:
                existing["serial_identity_hash"] = str(
                    serial_identity_hash
                ).strip().replace(":", "")

    existing = normalize_contact(existing)
    lan_final = (existing.get("lan_hash") or "").replace(":", "")
    ser_final = (existing.get("serial_hash") or "").replace(":", "")
    if lan_final and ser_final and lan_final == ser_final:
        existing.pop("serial_hash", None)

    file_key = contact_primary_hash(existing) or clean
    path = _contact_path(config_dir, file_key)
    with open(path, "w") as fh:
        json.dump(existing, fh, indent=2)
    _purge_stale_contact_files(config_dir, existing, file_key)
    if clean != file_key:
        _unlink_contact_file(config_dir, clean)
    return existing


def _unlink_contact_file(config_dir, peer_hash):
    """Delete one on-disk contact JSON file (internal; does not load/merge contacts)."""
    path = _contact_path(config_dir, peer_hash)
    if os.path.exists(path):
        os.unlink(path)
        return True
    return False


def delete_contact(config_dir, peer_hash):
    """Remove a saved contact by any stored hash (lan, serial, or legacy file key)."""
    clean = (peer_hash or "").strip().replace(":", "")
    if not clean:
        return False
    contact = find_contact_by_hash(config_dir, clean)
    if contact:
        removed = False
        for h in _contact_hashes(contact):
            if _unlink_contact_file(config_dir, h):
                removed = True
        return removed
    return _unlink_contact_file(config_dir, clean)


def _purge_stale_contact_files(config_dir, entry, keep_key):
    """Remove duplicate on-disk contact files after a hash or primary key change."""
    keep = (keep_key or "").replace(":", "")
    removed = []
    for h in _contact_hashes(entry or {}):
        if h and h != keep:
            if _unlink_contact_file(config_dir, h):
                removed.append(h)
    return removed


def _contact_dedup_keys(contact):
    """All keys that may refer to the same saved peer (incl. split lan/serial files)."""
    c = normalize_contact(contact or {})
    keys = []
    ident = (c.get("identity_hash") or "").replace(":", "")
    if ident:
        keys.append(f"ident:{ident}")
    lan = (c.get("lan_hash") or c.get("hash") or "").replace(":", "")
    serial = (c.get("serial_hash") or "").replace(":", "")
    if lan or serial:
        keys.append(f"h:{lan}:{serial}")
    name = (c.get("name") or "").strip().lower()
    if name and not _name_is_hash_like(c.get("name"), _contact_hashes(c)):
        keys.append(f"name:{name}")
    if not keys:
        keys.append(f"orphan:{lan or serial or 'unknown'}")
    return keys


def _contact_dedup_key(contact):
    return _contact_dedup_keys(contact)[0]


def _should_merge_contacts(primary, secondary):
    """True when two on-disk rows are the same peer (split lan/serial files, etc.)."""
    a = normalize_contact(primary or {})
    b = normalize_contact(secondary or {})
    if set(_contact_hashes(a)) & set(_contact_hashes(b)):
        return True
    a_ident = (a.get("identity_hash") or "").replace(":", "")
    b_ident = (b.get("identity_hash") or "").replace(":", "")
    if a_ident and b_ident and a_ident == b_ident:
        return True
    a_lan = (a.get("lan_hash") or "").replace(":", "")
    a_serial = (a.get("serial_hash") or "").replace(":", "")
    b_lan = (b.get("lan_hash") or "").replace(":", "")
    b_serial = (b.get("serial_hash") or "").replace(":", "")
    if not a_lan and not a_serial and a.get("hash"):
        a_lan = (a.get("hash") or "").replace(":", "")
    if not b_lan and not b_serial and b.get("hash"):
        b_lan = (b.get("hash") or "").replace(":", "")
    complementary = (
        (a_lan and b_serial and not a_serial and not b_lan)
        or (a_serial and b_lan and not a_lan and not b_serial)
    )
    if complementary and _names_related(a.get("name"), b.get("name")):
        return True
    a_name = (a.get("name") or "").strip().lower()
    b_name = (b.get("name") or "").strip().lower()
    if (
        complementary
        and a_name
        and b_name
        and a_name == b_name
        and not _name_is_hash_like(a.get("name"), _contact_hashes(a))
    ):
        return True
    return False


def _contact_field_empty(val):
    if val is None:
        return True
    if isinstance(val, str):
        return not val.strip()
    return False


def _merge_contact_entries(primary, secondary):
    """Merge two contact records that refer to the same peer."""
    out = normalize_contact(dict(primary or {}))
    other = normalize_contact(dict(secondary or {}))
    for key in (
        "hash", "lan_hash", "serial_hash",
        "identity_hash", "lan_identity_hash", "serial_identity_hash",
        "ip", "name",
    ):
        if _contact_field_empty(out.get(key)) and not _contact_field_empty(other.get(key)):
            out[key] = other.get(key)
    if out.get("port") is None and other.get("port") is not None:
        out["port"] = other.get("port")
    if other.get("custom_name"):
        out["custom_name"] = True
        if (other.get("name") or "").strip():
            out["name"] = other.get("name")
    if not out.get("lan_hash") and out.get("hash"):
        out["lan_hash"] = out["hash"]
    return normalize_contact(out)


def list_contacts(config_dir):
    raw = []
    base = contacts_dir(config_dir)
    if not os.path.isdir(base):
        return []
    for fname in sorted(os.listdir(base)):
        if fname.startswith("."):
            continue
        entry = load_contact(config_dir, fname)
        if entry:
            raw.append(entry)
    deduped = {}
    key_to_primary = {}
    orphans = []
    for entry in raw:
        entry = normalize_contact(dict(entry))
        keys = _contact_dedup_keys(entry)
        primary = None
        for key in keys:
            if key in key_to_primary:
                primary = key_to_primary[key]
                break
        if primary is None:
            primary = keys[0]
            deduped[primary] = entry
            for key in keys:
                key_to_primary[key] = primary
            continue
        merged = _merge_contact_entries(deduped[primary], entry)
        deduped[primary] = merged
        for key in keys:
            key_to_primary[key] = primary
        keep = contact_primary_hash(merged)
        for h in _contact_hashes(entry):
            if h and h != keep:
                orphans.append(h)
        for h in _contact_hashes(deduped[primary]):
            if h and h != keep:
                orphans.append(h)
    out = list(deduped.values())
    merged_out = []
    used = set()
    for i, entry in enumerate(out):
        if i in used:
            continue
        current = normalize_contact(dict(entry))
        for j in range(i + 1, len(out)):
            if j in used:
                continue
            if _should_merge_contacts(current, out[j]):
                current = _merge_contact_entries(current, out[j])
                keep = contact_primary_hash(current)
                for h in _contact_hashes(out[j]):
                    if h and h != keep:
                        orphans.append(h)
                used.add(j)
        merged_out.append(current)
    out = merged_out
    for entry in out:
        keep = contact_primary_hash(entry) or ""
        for h in _contact_hashes(entry):
            if h and h != keep:
                orphans.append(h)
    needs_persist = len(raw) > len(out) or bool(orphans)
    for entry in out:
        keep = contact_primary_hash(entry) or ""
        if needs_persist and keep:
            path = _contact_path(config_dir, keep)
            try:
                with open(path, "w") as fh:
                    json.dump(normalize_contact(entry), fh, indent=2)
            except OSError:
                pass
        _purge_stale_contact_files(config_dir, entry, keep)
    for orphan in set(orphans):
        keepers = {contact_primary_hash(c) for c in out}
        if orphan not in keepers:
            _unlink_contact_file(config_dir, orphan)
    return out


def migrate_contact_hash(
    config_dir,
    old_hash,
    new_hash,
    name=None,
    ip=None,
    port=None,
    identity_hash=None,
    via=None,
):
    """Update a saved contact when discovery supersedes an alias hash."""
    old_clean = (old_hash or "").strip().replace(":", "")
    new_clean = (new_hash or "").strip().replace(":", "")
    if not old_clean or not new_clean or old_clean == new_clean:
        return False
    entry = find_contact_by_hash(config_dir, old_clean) or load_contact(config_dir, old_clean)
    if not entry:
        return False
    entry = normalize_contact(dict(entry))
    transport = (via or "").strip().lower()
    is_serial = transport == "serial" or (
        not transport
        and old_clean == (entry.get("serial_hash") or "").replace(":", "")
        and old_clean != (entry.get("lan_hash") or entry.get("hash") or "").replace(":", "")
    )
    if is_serial:
        entry["serial_hash"] = new_clean
        if identity_hash:
            entry["serial_identity_hash"] = str(identity_hash).strip().replace(":", "")
    else:
        entry["lan_hash"] = new_clean
        entry["hash"] = new_clean
        if identity_hash:
            ident = str(identity_hash).strip().replace(":", "")
            entry["lan_identity_hash"] = ident
            entry["identity_hash"] = ident
        if ip is not None and str(ip).strip():
            entry["ip"] = str(ip).strip()
        if port is not None:
            try:
                entry["port"] = int(port)
            except (TypeError, ValueError):
                pass
    if name is not None and str(name).strip():
        entry["name"] = _resolve_contact_name(entry, name)
    file_key = contact_primary_hash(entry) or new_clean
    path = _contact_path(config_dir, file_key)
    with open(path, "w") as fh:
        json.dump(entry, fh, indent=2)
    if old_clean != file_key:
        _unlink_contact_file(config_dir, old_clean)
    return True


def migrate_contact_by_ip(config_dir, ip, new_hash, name=None, port=None, identity_hash=None):
    """Replace any saved contact on this LAN IP with the peer's current hash."""
    ip = (ip or "").strip()
    new_clean = (new_hash or "").strip().replace(":", "")
    if not ip or not new_clean:
        return []
    removed = []
    for contact in list_contacts(config_dir):
        if (contact.get("ip") or "").strip() != ip:
            continue
        old_key = contact_primary_hash(contact)
        prior = normalize_contact(dict(contact))
        updated = save_contact(
            config_dir,
            new_clean,
            name=_resolve_contact_name(prior, name),
            ip=ip,
            port=port,
            identity_hash=identity_hash,
            via="lan",
            lan_hash=new_clean,
            serial_hash=prior.get("serial_hash"),
            serial_identity_hash=prior.get("serial_identity_hash"),
            custom_name=bool(prior.get("custom_name")),
        )
        new_key = contact_primary_hash(updated) or new_clean
        if old_key and old_key != new_key:
            _unlink_contact_file(config_dir, old_key)
            removed.append(old_key)
    return removed


def _same_subnet(ip_a, ip_b):
    """True when two IPv4 addresses are on the same LAN scope for contact updates."""
    from chatxz.utils.lan_scope import same_lan_scope
    return same_lan_scope(ip_a, ip_b)


def should_update_contact_ip(contact_ip, new_ip, local_scope_ip=None):
    """Prefer pinned-LAN subnet IPs; ignore cross-subnet beacons when contact is local."""
    new_ip = (new_ip or "").strip()
    contact_ip = (contact_ip or "").strip()
    if not new_ip:
        return False
    if not contact_ip:
        return True
    if contact_ip == new_ip:
        return False
    scope = (local_scope_ip or "").strip()
    if not scope:
        return True
    new_local = _same_subnet(new_ip, scope)
    contact_local = _same_subnet(contact_ip, scope)
    if new_local and not contact_local:
        return True
    if new_local and contact_local:
        return True
    if not new_local and contact_local:
        return False
    return True


def update_contact_endpoint(
    config_dir,
    peer_hash,
    ip=None,
    port=None,
    identity_hash=None,
    peers_equivalent=None,
    name=None,
    local_scope_ip=None,
):
    """Refresh saved contact LAN endpoint when the same peer moves to a new IP."""
    clean = (peer_hash or "").strip().replace(":", "")
    if not clean:
        return None
    target_ip = (ip or "").strip()
    peer_name = (name or "").strip().lower()
    updated = None
    for contact in list_contacts(config_dir):
        ch = (contact.get("hash") or "").replace(":", "")
        ih = (contact.get("identity_hash") or "").replace(":", "")
        same = ch == clean
        if not same and peers_equivalent:
            same = peers_equivalent(ch, clean) or (ih and peers_equivalent(ih, clean))
        if not same and identity_hash:
            ident = str(identity_hash).strip().replace(":", "")
            same = ih == ident or ch == ident
        if not same and peer_name:
            cn = (contact.get("name") or "").strip().lower()
            if cn and (_names_related(cn, peer_name) or cn == peer_name):
                same = True
        if not same:
            continue
        contact_ip = (contact.get("ip") or "").strip()
        lan = (contact.get("lan_hash") or ch or "").replace(":", "")
        needs_hash = clean and lan != clean and ch == lan
        if needs_hash:
            updated = save_contact(
                config_dir,
                clean,
                name=_resolve_contact_name(contact, name),
                ip=target_ip or contact_ip or None,
                port=port if port is not None else contact.get("port"),
                identity_hash=identity_hash or ih or None,
                via="lan",
                lan_hash=clean,
                serial_hash=contact.get("serial_hash"),
                serial_identity_hash=contact.get("serial_identity_hash"),
                custom_name=bool(contact.get("custom_name")),
            )
            break
        if target_ip and should_update_contact_ip(contact_ip, target_ip, local_scope_ip):
            updated = save_contact(
                config_dir,
                ch or clean,
                name=_resolve_contact_name(contact, name),
                ip=target_ip,
                port=port if port is not None else contact.get("port"),
                identity_hash=identity_hash or ih or None,
                via="lan",
                lan_hash=lan or clean,
                serial_hash=contact.get("serial_hash"),
                serial_identity_hash=contact.get("serial_identity_hash"),
                custom_name=bool(contact.get("custom_name")),
            )
        break
    return updated


def contact_connect_meta(config_dir, peer_hash, peers_equivalent):
    """Return (ip, port) stored on a saved contact, if any."""
    clean = (peer_hash or "").strip().replace(":", "")
    for contact in list_contacts(config_dir):
        ch = (contact.get("hash") or "").replace(":", "")
        ih = (contact.get("identity_hash") or "").replace(":", "")
        if peers_equivalent(ch, clean) or (ih and peers_equivalent(ih, clean)):
            ip = (contact.get("ip") or "").strip() or None
            port = contact.get("port") or 8742
            if ip:
                return ip, port
    return None, None
