"""push_relay.config -- DPAPI VAPID + subs.json helpers (Windows only)."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any


def state_dir(client: str | None = None) -> Path:
    base = Path(os.path.expanduser("~")) / ".push-relay"
    if client:
        return base / client
    return base


def vapid_path() -> Path:
    return state_dir() / "vapid.json.dpapi"


def subs_path(client: str) -> Path:
    return state_dir(client) / "subs.json"


def load_vapid() -> dict[str, str]:
    """Decrypt and parse the DPAPI-protected VAPID JSON."""
    import win32crypt  # type: ignore  # pywin32

    p = vapid_path()
    if not p.exists():
        raise FileNotFoundError(f"VAPID not configured at {p}. Run tools/new-vapid.py.")
    blob = base64.b64decode(p.read_text(encoding="ascii").strip())
    _desc, plain = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
    return json.loads(plain.decode("utf-8"))


def save_vapid(public_key: str, private_key: str, subject: str) -> Path:
    """Encrypt and write the VAPID JSON to ~/.push-relay/vapid.json.dpapi."""
    import win32crypt  # type: ignore

    p = vapid_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"publicKey": public_key, "privateKey": private_key, "subject": subject},
        separators=(",", ":"),
    ).encode("utf-8")
    blob = win32crypt.CryptProtectData(payload, "push-relay vapid", None, None, None, 0)
    p.write_text(base64.b64encode(blob).decode("ascii"), encoding="ascii")
    return p


def load_subs(client: str) -> list[dict[str, Any]]:
    p = subs_path(client)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_subs(client: str, subs: list[dict[str, Any]]) -> None:
    p = subs_path(client)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(subs, separators=(",", ":")), encoding="utf-8")


def add_sub(
    client: str, sub: dict[str, Any], label: str | None = None
) -> list[dict[str, Any]]:
    """Append a PushSubscription, dedup by endpoint."""
    if not sub.get("endpoint", "").startswith("https://"):
        raise ValueError("invalid endpoint")
    keys = sub.get("keys") or {}
    if not keys.get("p256dh") or not keys.get("auth"):
        raise ValueError("missing keys.p256dh or keys.auth")
    from datetime import datetime, timezone

    entry = {
        "endpoint": sub["endpoint"],
        "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]},
        "added": datetime.now(timezone.utc).isoformat(),
    }
    if label:
        entry["label"] = label
    existing = [s for s in load_subs(client) if s.get("endpoint") != sub["endpoint"]]
    existing.append(entry)
    save_subs(client, existing)
    return existing
