"""new-vapid.py -- Generate a fresh VAPID keypair, encrypt to ~/.push-relay/vapid.json.dpapi.

Usage:
    py tools/new-vapid.py mailto:you@example.com
"""
from __future__ import annotations

import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from push_relay import b64u_encode, save_vapid


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].startswith("mailto:"):
        print("Usage: py tools/new-vapid.py mailto:you@example.com", file=sys.stderr)
        return 2
    subject = sys.argv[1]

    priv = ec.generate_private_key(ec.SECP256R1())
    nums = priv.private_numbers()
    pub_bytes = priv.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    public_b64u = b64u_encode(pub_bytes)
    private_b64u = b64u_encode(nums.private_value.to_bytes(32, "big"))

    p = save_vapid(public_b64u, private_b64u, subject)
    print(f"Wrote {p}")
    print()
    print("VAPID public (paste into pwa/config.js as window.VAPID_PUBLIC):")
    print(f"  {public_b64u}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
