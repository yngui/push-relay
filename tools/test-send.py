"""Live FCM verification of push_relay.send.

Reads VAPID + subs from `~/.push-relay/<client>/` (default client:
windows-toast) and sends one synthetic push to each subscription.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running without install
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from push_relay import load_subs, load_vapid, send_web_push  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--client", default="windows-toast")
    args = p.parse_args()

    try:
        vapid = load_vapid()
    except Exception as e:
        print(f"VAPID load failed: {e}", file=sys.stderr)
        return 1

    subs = load_subs(args.client)
    if not subs:
        print(f"no subs in ~/.push-relay/{args.client}/subs.json", file=sys.stderr)
        return 1

    success = 0
    for i, sub in enumerate(subs):
        print(f"\n[{i}] {sub.get('label','-')}: {sub['endpoint'][:80]}...")
        payload = json.dumps(
            {
                "title": "push_relay live test",
                "body": f"Direct push #{i} from test-send.py",
                "source": {"app": "push_relay", "host": "test-py"},
                "sent": datetime.now(timezone.utc).isoformat(),
            },
            separators=(",", ":"),
        )
        code, body = send_web_push(
            sub["endpoint"],
            sub["keys"]["p256dh"],
            sub["keys"]["auth"],
            payload,
            vapid,
            urgency="high",
        )
        print(f"  status={code} body={body!r}")
        if 200 <= code < 300:
            success += 1
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
