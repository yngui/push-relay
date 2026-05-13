"""extract-app-logo.py -- Capture a Windows app's logo PNG for push payloads.

Watches the UserNotificationListener for the next toast matching --app-name
(regex on display name) or --aumid (exact). When matched, reads the source
app's logo via DisplayInfo.GetLogo(Size) and writes it to --out.

Modes:
  watch (default): prime current toasts, wait for a NEW matching toast.
  --snapshot     : match against currently-visible toasts only (one shot).

Usage:
    py tools/extract-app-logo.py --app-name "^Visual Studio Code$" \\
        --out pwa/icons/apps/vscode.png
    py tools/extract-app-logo.py --aumid "Microsoft.VisualStudio.Code_..." \\
        --out pwa/icons/apps/vscode.png --timeout 90

Tip: a reliable VS Code trigger is `code --install-extension nonexistent.foo`.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import sys
import time
from pathlib import Path


async def _open_listener():
    from winsdk.windows.ui.notifications.management import (  # type: ignore
        UserNotificationListener,
        UserNotificationListenerAccessStatus,
    )
    from winsdk.windows.ui.notifications import NotificationKinds  # type: ignore

    listener = UserNotificationListener.current
    status = listener.get_access_status()
    if status != UserNotificationListenerAccessStatus.ALLOWED:
        status = await listener.request_access_async()
    if status != UserNotificationListenerAccessStatus.ALLOWED:
        raise SystemExit(f"UserNotificationListener access denied: {status}")
    return listener, NotificationKinds.TOAST


async def _read_stream_ref_to_bytes(stream_ref) -> bytes:
    from winsdk.windows.storage.streams import DataReader  # type: ignore

    stream = await stream_ref.open_read_async()
    return await _read_stream_to_bytes(stream)


async def _read_stream_to_bytes(stream) -> bytes:
    from winsdk.windows.storage.streams import DataReader  # type: ignore

    size = stream.size
    reader = DataReader(stream)
    await reader.load_async(size)
    buf = bytearray(size)
    reader.read_bytes(buf)
    return bytes(buf)


def _info(n) -> tuple[str, str]:
    try:
        ai = n.app_info
        return (ai.app_user_model_id or "", ai.display_info.display_name or "")
    except Exception:
        return ("", "")


def _match(n, aumid: str | None, name_pat: re.Pattern[str] | None) -> bool:
    a, d = _info(n)
    if aumid and a == aumid:
        return True
    if name_pat and name_pat.match(d):
        return True
    return False


async def _extract_logo(n, size_px: int) -> bytes:
    from winsdk.windows.foundation import Size  # type: ignore

    logo_ref = n.app_info.display_info.get_logo(Size(float(size_px), float(size_px)))
    if logo_ref is not None:
        try:
            return await _read_stream_ref_to_bytes(logo_ref)
        except Exception as e:
            print(f"  AppDisplayInfo.GetLogo stream read failed: {e}; trying shortcut fallback",
                  file=sys.stderr)
    else:
        print("  AppDisplayInfo.GetLogo returned None (non-packaged app); "
              "trying shortcut fallback", file=sys.stderr)

    a, d = _info(n)
    return await _logo_via_shortcut(d, size_px)


def _find_shortcut(display_name: str) -> Path | None:
    """Find a Start Menu .lnk whose filename matches the display name."""
    roots = [
        Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
        Path(os.environ.get("ProgramData", "")) / "Microsoft/Windows/Start Menu/Programs",
    ]
    candidates: list[Path] = []
    for r in roots:
        if r.exists():
            for p in r.rglob("*.lnk"):
                if p.stem.lower() == display_name.lower():
                    candidates.insert(0, p)  # exact match first
                elif display_name.lower() in p.stem.lower():
                    candidates.append(p)
    return candidates[0] if candidates else None


def _resolve_shortcut_target(lnk: Path) -> str | None:
    cmd = [
        "powershell.exe", "-NoProfile", "-Command",
        f"(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}').TargetPath",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    target = (r.stdout or "").strip()
    return target or None


async def _logo_via_shortcut(display_name: str, size_px: int) -> bytes:
    from winsdk.windows.storage import StorageFile  # type: ignore
    from winsdk.windows.storage.fileproperties import (  # type: ignore
        ThumbnailMode,
        ThumbnailOptions,
    )

    lnk = _find_shortcut(display_name)
    if not lnk:
        raise RuntimeError(f"No Start Menu shortcut found for {display_name!r}")
    target = _resolve_shortcut_target(lnk)
    if not target or not Path(target).exists():
        raise RuntimeError(f"Shortcut target unresolved or missing: {target!r}")
    print(f"  fallback: extracting icon from {target}", file=sys.stderr)
    sf = await StorageFile.get_file_from_path_async(target)
    thumb = await sf.get_thumbnail_async(
        ThumbnailMode.SINGLE_ITEM, size_px, ThumbnailOptions.RESIZE_THUMBNAIL
    )
    if thumb is None:
        raise RuntimeError("GetThumbnailAsync returned None")
    return await _read_stream_to_bytes(thumb)


async def _watch(
    listener, kind_toast, aumid: str | None, name_pat: re.Pattern[str] | None,
    timeout_s: float, poll_s: float,
):
    initial = await listener.get_notifications_async(kind_toast)
    seen = {int(n.id) for n in initial}
    print(f"Primed {len(seen)} active toast(s). Waiting up to {int(timeout_s)}s "
          f"for a new matching toast — trigger one now.", flush=True)

    other_seen: dict[str, str] = {}  # aumid -> display
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        await asyncio.sleep(poll_s)
        try:
            current = await listener.get_notifications_async(kind_toast)
        except Exception as e:
            print(f"  poll failed: {e}", file=sys.stderr)
            continue
        for n in current:
            nid = int(n.id)
            if nid in seen:
                continue
            seen.add(nid)
            a, d = _info(n)
            if _match(n, aumid, name_pat):
                print(f"Matched: display={d!r} aumid={a!r}", flush=True)
                return n
            other_seen[a] = d
            print(f"  new toast (no match): display={d!r} aumid={a!r}", flush=True)

    print("Timed out waiting for a matching toast.", file=sys.stderr)
    if other_seen:
        print("Saw these non-matching toasts during the wait:", file=sys.stderr)
        for a, d in other_seen.items():
            print(f"  display={d!r}  aumid={a!r}", file=sys.stderr)
    return None


async def _snapshot(listener, kind_toast, aumid, name_pat):
    toasts = await listener.get_notifications_async(kind_toast)
    seen: list[tuple[str, str]] = []
    for n in toasts:
        a, d = _info(n)
        seen.append((a, d))
        if _match(n, aumid, name_pat):
            return n
    print("No matching active toast. Currently visible:", file=sys.stderr)
    for a, d in seen:
        print(f"  display={d!r}  aumid={a!r}", file=sys.stderr)
    return None


async def main_async(args) -> int:
    name_pat = re.compile(args.app_name) if args.app_name else None

    if args.offline:
        if not name_pat:
            print("--offline requires --app-name", file=sys.stderr)
            return 2
        return await _run_offline(name_pat, args)

    listener, kind_toast = await _open_listener()

    if args.snapshot:
        match = await _snapshot(listener, kind_toast, args.aumid, name_pat)
    else:
        match = await _watch(
            listener, kind_toast, args.aumid, name_pat,
            float(args.timeout), float(args.poll),
        )
    if not match:
        return 2

    try:
        data = await _extract_logo(match, args.size)
    except Exception as e:
        print(f"icon extraction failed: {e}", file=sys.stderr)
        return 3

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    a, _ = _info(match)
    print(f"Wrote {out} ({len(data)} bytes)")
    print()
    print("Add to clients/windows-toast/windows_toast.py:")
    print(f'  APP_ICON_AUMID[{a!r}] = "icons/apps/{out.stem}.png"')
    return 0


def _list_start_apps() -> list[tuple[str, str]]:
    """Return [(Name, AppID), ...] from Get-StartApps."""
    cmd = [
        "powershell.exe", "-NoProfile", "-Command",
        "Get-StartApps | ConvertTo-Json -Compress",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, check=True)
    import json
    data = json.loads(r.stdout or "[]")
    if isinstance(data, dict):
        data = [data]
    return [(d.get("Name", ""), d.get("AppID", "")) for d in data]


async def _run_offline(name_pat: re.Pattern[str], args) -> int:
    apps = _list_start_apps()
    matches = [(n, a) for n, a in apps if name_pat.match(n)]
    if not matches:
        print(f"No Start menu app matches {name_pat.pattern!r}.", file=sys.stderr)
        print("Available (first 20):", file=sys.stderr)
        for n, a in apps[:20]:
            print(f"  name={n!r}  aumid={a!r}", file=sys.stderr)
        return 2
    if len(matches) > 1:
        print("Multiple matches; picking first:", file=sys.stderr)
        for n, a in matches:
            print(f"  name={n!r}  aumid={a!r}", file=sys.stderr)
    name, aumid = matches[0]
    print(f"Selected: name={name!r} aumid={aumid!r}")
    try:
        data = await _logo_via_shortcut(name, args.size)
    except Exception as e:
        print(f"icon extraction failed: {e}", file=sys.stderr)
        return 3
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"Wrote {out} ({len(data)} bytes)")
    print()
    print("Add to clients/windows-toast/windows_toast.py:")
    print(f'  APP_ICON_AUMID[{aumid!r}] = "icons/apps/{out.stem}.png"')
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--app-name", help="Regex matched against display name")
    p.add_argument("--aumid", help="Exact AUMID match")
    p.add_argument("--out", required=True, help="Output PNG path")
    p.add_argument("--size", type=int, default=192, help="Logo size px (default 192)")
    p.add_argument("--snapshot", action="store_true",
                   help="Match against currently-visible toasts (default: watch for next)")
    p.add_argument("--offline", action="store_true",
                   help="Skip toast listener; resolve via Get-StartApps + shortcut icon")
    p.add_argument("--timeout", type=float, default=60.0,
                   help="Watch-mode timeout seconds (default 60)")
    p.add_argument("--poll", type=float, default=1.5,
                   help="Watch-mode poll interval seconds (default 1.5)")
    args = p.parse_args()
    if not args.app_name and not args.aumid:
        p.error("provide --app-name or --aumid")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
