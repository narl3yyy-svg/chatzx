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
