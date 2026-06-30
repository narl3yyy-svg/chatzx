"""Forward RNS call signaling and media to the Rust chatxz application."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

APP_URL = os.environ.get("CHATXZ_APP_URL", "http://127.0.0.1:8742").rstrip("/")


def _post(path: str, payload: dict, timeout: float = 3.0) -> bool:
    url = f"{APP_URL}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except urllib.error.URLError as exc:
        print(f"[rnsd] POST {path} failed: {exc}")
        return False


def forward_signaling(peer_hash: str, content: str) -> bool:
    return _post("/internal/signaling", {"peer": peer_hash, "content": content})


def forward_media(peer_hash: str, data: bytes) -> bool:
    return _post(
        "/internal/media",
        {"peer": peer_hash, "data": data.hex()},
        timeout=2.0,
    )