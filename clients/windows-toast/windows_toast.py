"""windows-toast client: forward Windows Action Center toasts to push_relay subs.

Single Python process. Polls UserNotificationListener every 2s, dedups, filters
by app regex, encrypts + sends via push_relay to each registered subscription.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import socket
import sys
import time
from collections import deque
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable

from push_relay import load_subs, load_vapid, send_web_push, state_dir

# ---- Configuration --------------------------------------------------------
CLIENT_NAME = "windows-toast"
APP_REGEX = re.compile(r"^Visual Studio Code( - Insiders)?$")
BODY_REGEX: re.Pattern[str] | None = None
POLL_INTERVAL_SEC = 2
RATE_MAX_PER_WINDOW = 10
RATE_WINDOW_SEC = 60
SEEN_CAP = 1000
URGENCY = "high"  # RFC 8030 max; wakes phone from Doze for prompt delivery.

# Per-app icon mapping. Values are paths relative to the PWA root, served from
# pwa/icons/apps/<file>.png on GitHub Pages. Only set entries for icons that
# actually exist in the repo; the SW falls back to the default icon when the
# payload omits `icon`, so missing apps are safe. Keying preference:
#   1) exact AUMID match (stable across locales)  -- APP_ICON_AUMID
#   2) display-name regex fallback                -- APP_ICON_DISPLAY
APP_ICON_AUMID: dict[str, str] = {
    "Microsoft.VisualStudioCode": "icons/apps/vscode.png",
}
APP_ICON_DISPLAY: list[tuple[re.Pattern[str], str]] = [
    # (re.compile(r"^Visual Studio Code$"), "icons/apps/vscode.png"),
    # (re.compile(r"^Visual Studio Code - Insiders$"), "icons/apps/vscode-insiders.png"),
]

# Encrypted payload ceiling is 4078 (push_relay.send). Leave headroom for
# encryption overhead -- ensure the JSON we hand to send_web_push stays under:
PAYLOAD_BUDGET_BYTES = 4000


def resolve_icon(aumid: str, app: str) -> str | None:
    if aumid and aumid in APP_ICON_AUMID:
        return APP_ICON_AUMID[aumid]
    for pat, path in APP_ICON_DISPLAY:
        if pat.match(app or ""):
            return path
    return None

DIR = state_dir(CLIENT_NAME)
DIR.mkdir(parents=True, exist_ok=True)
NOTIFY_LOG = DIR / "notify.log"
SEEN_FILE = DIR / "seen-ids.txt"
LISTENER_LOG = DIR / "listener.log"

# ---- Logging --------------------------------------------------------------
log = logging.getLogger("windows-toast")
log.setLevel(logging.INFO)
_handler = RotatingFileHandler(LISTENER_LOG, maxBytes=512_000, backupCount=2, encoding="utf-8")
_handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s"))
log.addHandler(_handler)


# ---- Seen-id persistence --------------------------------------------------
def load_seen() -> set[int]:
    if not SEEN_FILE.exists():
        return set()
    out: set[int] = set()
    for line in SEEN_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.isdigit():
            out.add(int(line))
    return out


def save_seen(seen: set[int]) -> None:
    ids = sorted(seen, reverse=True)[:SEEN_CAP]
    SEEN_FILE.write_text("\n".join(str(i) for i in ids), encoding="utf-8")


# ---- Push -----------------------------------------------------------------
def _build_payload(
    title: str, body: str, app: str, icon: str | None
) -> str:
    """Build the encrypted-payload JSON, fitting under PAYLOAD_BUDGET_BYTES.

    Truncation order on overflow: body, then drop icon. A warning is logged.

    `source` is a free-form mapping of label -> string. Consumers (SW, detail
    page) iterate it in insertion order.
    """
    source: dict[str, str] = {}
    if app:
        source["app"] = app
    host = socket.gethostname()
    if host:
        source["host"] = host

    base: dict = {
        "title": title,
        "body": body or "",
        "source": source,
        "sent": datetime.now(timezone.utc).isoformat(),
    }
    if icon:
        base["icon"] = icon

    def dumps(obj: dict) -> str:
        return json.dumps(obj, separators=(",", ":"))

    out = dumps(base)
    if len(out.encode("utf-8")) <= PAYLOAD_BUDGET_BYTES:
        return out

    # Try truncating body.
    over = len(out.encode("utf-8")) - PAYLOAD_BUDGET_BYTES
    body_bytes = base["body"].encode("utf-8")
    if len(body_bytes) > over + 1:
        base["body"] = body_bytes[: max(0, len(body_bytes) - over - 1)].decode(
            "utf-8", errors="ignore"
        ) + "\u2026"
        out = dumps(base)
        if len(out.encode("utf-8")) <= PAYLOAD_BUDGET_BYTES:
            log.warning("payload truncated body to fit budget")
            return out

    # Drop icon as last resort.
    if "icon" in base:
        base.pop("icon")
        out = dumps(base)
        log.warning("payload dropped icon to fit budget")
    return out


def _push(
    title: str, body: str, app: str, vapid: dict[str, str], icon: str | None = None
) -> None:
    subs = load_subs(CLIENT_NAME)
    if not subs:
        log.warning("push skipped: no subs in %s", DIR / "subs.json")
        return
    payload = _build_payload(title, body, app, icon)
    ok = 0
    failures: list[str] = []
    for s in subs:
        label = s.get("label", "")
        try:
            code, msg = send_web_push(
                s["endpoint"], s["keys"]["p256dh"], s["keys"]["auth"], payload, vapid,
                urgency=URGENCY,
            )
            if 200 <= code < 300:
                ok += 1
            else:
                failures.append(f"{label or '(no-label)'}={code}")
                log.info("push failed %s status=%s body=%r", label, code, msg[:120])
        except Exception as e:  # noqa: BLE001
            failures.append(f"{label}=throw:{e!r}")
            log.exception("push subprocess failed for %s", label)
    extra = f" failed=[{'; '.join(failures)}]" if failures else ""
    log.info("Push sent: ok=%d total=%d%s", ok, len(subs), extra)


# ---- WinRT bridge ---------------------------------------------------------
async def winrt_runtime():
    """Return (listener, NotificationKinds.toast, ToastGenericBinding)."""
    from winsdk.windows.ui.notifications.management import (  # type: ignore
        UserNotificationListener,
        UserNotificationListenerAccessStatus,
    )
    from winsdk.windows.ui.notifications import (  # type: ignore
        NotificationKinds,
        KnownNotificationBindings,
    )

    listener = UserNotificationListener.current
    status = listener.get_access_status()
    if status != UserNotificationListenerAccessStatus.ALLOWED:
        log.info("Requesting UserNotificationListener access...")
        status = await listener.request_access_async()
        log.info("RequestAccess -> %s", status)
    if status != UserNotificationListenerAccessStatus.ALLOWED:
        log.error("Access not granted; exiting")
        sys.exit(2)
    return listener, NotificationKinds.TOAST, KnownNotificationBindings


def extract_text(notification, KnownNotificationBindings) -> tuple[str, str]:
    title = ""
    body = ""
    try:
        binding = notification.notification.visual.get_binding(
            KnownNotificationBindings.toast_generic
        )
        if binding:
            texts = list(binding.get_text_elements())
            if texts:
                title = texts[0].text or ""
            if len(texts) > 1:
                body = "\n".join(t.text or "" for t in texts[1:])
    except Exception as e:  # noqa: BLE001
        log.warning("extract_text failed: %s", e)
    return title, body


def write_notify_log(notification, app: str, title: str, body: str) -> None:
    obj = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "app": app,
        "title": title,
        "body": body,
        "raw_id": int(notification.id),
    }
    try:
        with NOTIFY_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, separators=(",", ":")) + "\n")
    except Exception as e:  # noqa: BLE001
        log.warning("notify.log append failed: %s", e)


# ---- Main loop ------------------------------------------------------------
async def main() -> int:
    log.info("Startup. client=%s state=%s", CLIENT_NAME, DIR)
    try:
        vapid = load_vapid()
    except Exception as e:  # noqa: BLE001
        log.error("VAPID load failed: %s", e)
        return 2

    listener, kind_toast, known = await winrt_runtime()
    log.info("Push enabled. host=%s", socket.gethostname())

    seen = load_seen()
    push_times: deque[float] = deque(maxlen=RATE_MAX_PER_WINDOW * 4)

    # Prime initial snapshot.
    initial = await listener.get_notifications_async(kind_toast)
    log.info("Initial notifications: %d", len(initial))
    for n in initial:
        seen.add(int(n.id))
    save_seen(seen)

    log.info("Entering polling loop (interval=%ss).", POLL_INTERVAL_SEC)
    save_counter = 0
    while True:
        try:
            current = await listener.get_notifications_async(kind_toast)
        except Exception as e:  # noqa: BLE001
            log.warning("Poll iteration failed: %s", e)
            await asyncio.sleep(POLL_INTERVAL_SEC)
            continue

        for n in current:
            nid = int(n.id)
            if nid in seen:
                continue
            seen.add(nid)
            try:
                app = n.app_info.display_info.display_name or ""
            except Exception:
                app = ""
            title, body = extract_text(n, known)
            log.info("SEEN id=%d app=%r title=%r", nid, app, title[:60])
            if not APP_REGEX.match(app):
                continue
            if BODY_REGEX and not BODY_REGEX.search(f"{title}\n{body}"):
                continue

            # Rate guard.
            now = time.monotonic()
            while push_times and now - push_times[0] > RATE_WINDOW_SEC:
                push_times.popleft()
            if len(push_times) >= RATE_MAX_PER_WINDOW:
                log.info("Rate-limited (>=%d/%ds); dropped", RATE_MAX_PER_WINDOW, RATE_WINDOW_SEC)
                continue
            push_times.append(now)

            try:
                aumid = n.app_info.app_user_model_id or ""
            except Exception:
                aumid = ""
            icon = resolve_icon(aumid, app)
            log.info("MATCH id=%d app=%s title=%s icon=%s", nid, app, title, icon or "-")
            write_notify_log(n, app, title, body)
            _push(title, body, app, vapid, icon=icon)

        save_counter += 1
        if save_counter >= 30:
            save_seen(seen)
            save_counter = 0
        await asyncio.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
