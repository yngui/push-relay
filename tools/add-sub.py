"""add-sub.py -- Append a PushSubscription to a client's subs.json.

Usage:
    py tools/add-sub.py [--client windows-toast] [--label "my-phone"] [--json '{"endpoint":...}']

If --json is omitted, reads from clipboard.
"""
from __future__ import annotations

import argparse
import json
import sys

from push_relay import add_sub, subs_path


def get_clipboard() -> str:
    import subprocess

    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout.strip()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--client", default="windows-toast")
    p.add_argument("--label", default=None)
    p.add_argument("--json", default=None, help="PushSubscription JSON (else clipboard)")
    args = p.parse_args()

    raw = args.json or get_clipboard()
    if not raw:
        print("No subscription JSON. Pass --json or copy to clipboard.", file=sys.stderr)
        return 2
    try:
        sub = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON: {e}", file=sys.stderr)
        return 2

    subs = add_sub(args.client, sub, args.label)
    print(f"Wrote {subs_path(args.client)} ({len(subs)} sub(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
