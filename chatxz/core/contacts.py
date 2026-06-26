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


def load_contact(config_dir, filename):
    path = os.path.join(contacts_dir(config_dir), filename)
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as fh:
            raw = fh.read().strip()
        if not raw:
            return {"hash": filename, "name": filename}
        if raw.startswith("{"):
            data = json.loads(raw)
            if isinstance(data, dict):
                data.setdefault("hash", filename)
                data.setdefault("name", filename)
                return data
        return {"hash": filename, "name": raw}
    except Exception:
        return {"hash": filename, "name": filename}


def save_contact(config_dir, peer_hash, name=None, ip=None, port=None, identity_hash=None):
    clean = (peer_hash or "").strip().replace(":", "")
    if not clean:
        raise ValueError("hash required")
    existing = load_contact(config_dir, clean) or {"hash": clean, "name": clean}
    if name is not None and str(name).strip():
        existing["name"] = str(name).strip()
    if ip is not None and str(ip).strip():
        existing["ip"] = str(ip).strip()
    if port is not None:
        try:
            existing["port"] = int(port)
        except (TypeError, ValueError):
            pass
    if identity_hash is not None and str(identity_hash).strip():
        existing["identity_hash"] = str(identity_hash).strip().replace(":", "")
    path = _contact_path(config_dir, clean)
    with open(path, "w") as fh:
        json.dump(existing, fh, indent=2)
    return existing


def delete_contact(config_dir, peer_hash):
    path = _contact_path(config_dir, peer_hash)
    if os.path.exists(path):
        os.unlink(path)
        return True
    return False


def list_contacts(config_dir):
    out = []
    base = contacts_dir(config_dir)
    for fname in sorted(os.listdir(base)):
        if fname.startswith("."):
            continue
        entry = load_contact(config_dir, fname)
        if entry:
            out.append(entry)
    return out


def migrate_contact_by_ip(config_dir, ip, new_hash, name=None, port=None, identity_hash=None):
    """Replace any saved contact on this LAN IP with the peer's current hash."""
    ip = (ip or "").strip()
    new_clean = (new_hash or "").strip().replace(":", "")
    if not ip or not new_clean:
        return []
    removed = []
    matched = False
    for contact in list_contacts(config_dir):
        if (contact.get("ip") or "").strip() != ip:
            continue
        matched = True
        old_hash = (contact.get("hash") or "").replace(":", "")
        if old_hash and old_hash != new_clean:
            delete_contact(config_dir, old_hash)
            removed.append(old_hash)
    if matched:
        save_contact(
            config_dir,
            new_clean,
            name=name,
            ip=ip,
            port=port,
            identity_hash=identity_hash,
        )
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
            if cn and cn == peer_name:
                same = True
        if not same:
            continue
        contact_ip = (contact.get("ip") or "").strip()
        if target_ip and should_update_contact_ip(contact_ip, target_ip, local_scope_ip):
            updated = save_contact(
                config_dir,
                ch,
                name=contact.get("name"),
                ip=target_ip,
                port=port if port is not None else contact.get("port"),
                identity_hash=identity_hash or ih or None,
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
