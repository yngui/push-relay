"""push_relay.send -- Web Push (RFC 8291 aes128gcm) + VAPID (RFC 8292) sender.

Pure Python: cryptography + requests. Used by every client.
"""
from __future__ import annotations

import base64
import json
import os
import time
from typing import Any
from urllib.parse import urlsplit

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def b64u_decode(s: str) -> bytes:
    s = s.replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s)


def b64u_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _vapid_jwt(endpoint: str, vapid_priv_b64u: str, subject: str) -> str:
    aud = "{u.scheme}://{u.netloc}".format(u=urlsplit(endpoint))
    header = {"typ": "JWT", "alg": "ES256"}
    claims = {"aud": aud, "exp": int(time.time()) + 12 * 3600, "sub": subject}
    signing_input = (
        b64u_encode(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + b64u_encode(json.dumps(claims, separators=(",", ":")).encode())
    )

    d = int.from_bytes(b64u_decode(vapid_priv_b64u), "big")
    priv = ec.derive_private_key(d, ec.SECP256R1())
    der_sig = priv.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))

    # Convert DER -> raw r||s (64 bytes) for JWS.
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    r, s = decode_dss_signature(der_sig)
    raw_sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return signing_input + "." + b64u_encode(raw_sig)


def send_web_push(
    endpoint: str,
    p256dh: str,
    auth: str,
    payload: bytes | str,
    vapid: dict[str, str],
    *,
    ttl_sec: int = 60,
    urgency: str = "normal",
    timeout_sec: int = 10,
) -> tuple[int, str]:
    """Send one Web Push. Returns (http_status, body)."""
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    if len(payload) > 4078:
        raise ValueError("payload too large for one record")
    if urgency not in ("very-low", "low", "normal", "high"):
        raise ValueError(f"invalid urgency: {urgency!r}")

    ua_pub_bytes = b64u_decode(p256dh)
    if len(ua_pub_bytes) != 65 or ua_pub_bytes[0] != 0x04:
        raise ValueError("invalid p256dh")
    auth_secret = b64u_decode(auth)
    if len(auth_secret) != 16:
        raise ValueError("invalid auth")

    # 1. Ephemeral application server keypair.
    as_priv = ec.generate_private_key(ec.SECP256R1())
    as_pub_bytes = as_priv.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )

    # 2. UA public key import + ECDH.
    ua_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub_bytes)
    ecdh_secret = as_priv.exchange(ec.ECDH(), ua_pub)

    # 3. RFC 8291 HKDF chain.
    salt = os.urandom(16)
    key_info = b"WebPush: info\x00" + ua_pub_bytes + as_pub_bytes
    ikm2 = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=auth_secret, info=key_info
    ).derive(ecdh_secret)
    cek = HKDF(
        algorithm=hashes.SHA256(),
        length=16,
        salt=salt,
        info=b"Content-Encoding: aes128gcm\x00",
    ).derive(ikm2)
    nonce = HKDF(
        algorithm=hashes.SHA256(),
        length=12,
        salt=salt,
        info=b"Content-Encoding: nonce\x00",
    ).derive(ikm2)

    # 4. Pad payload (last record marker = 0x02).
    padded = payload + b"\x02"

    # 5. AES-128-GCM encrypt.
    ct_and_tag = AESGCM(cek).encrypt(nonce, padded, None)

    # 6. aes128gcm body: salt(16) || rs(4 BE = 4096) || idlen(1=65) || keyid(65) || ct+tag
    body = (
        salt
        + (4096).to_bytes(4, "big")
        + bytes([65])
        + as_pub_bytes
        + ct_and_tag
    )

    # 7. VAPID JWT + headers.
    jwt = _vapid_jwt(endpoint, vapid["privateKey"], vapid["subject"])
    headers = {
        "TTL": str(ttl_sec),
        "Urgency": urgency,
        "Content-Encoding": "aes128gcm",
        "Authorization": f"vapid t={jwt}, k={vapid['publicKey']}",
        "Content-Type": "application/octet-stream",
    }
    r = requests.post(endpoint, data=body, headers=headers, timeout=timeout_sec)
    return r.status_code, r.text
