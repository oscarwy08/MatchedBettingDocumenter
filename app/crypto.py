"""mbd1. blobs: HMAC + SHAKE stream. Used for Friends views and device snapshots."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets


def _key(secret: str) -> bytes:
    return hashlib.sha256(secret.encode("utf-8")).digest()


def encrypt_bytes(secret: str, raw: bytes) -> str:
    key = _key(secret)
    nonce = secrets.token_bytes(16)
    stream = hashlib.shake_256(key + nonce).digest(len(raw))
    cipher = bytes(a ^ b for a, b in zip(raw, stream))
    tag = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    blob = nonce + tag + cipher
    return "mbd1." + base64.urlsafe_b64encode(blob).decode("ascii")


def decrypt_bytes(secret: str, blob: str) -> bytes:
    if not (blob or "").startswith("mbd1."):
        raise ValueError("That payload is not encrypted.")
    data = base64.urlsafe_b64decode(blob[5:].encode("ascii"))
    if len(data) < 48:
        raise ValueError("That payload is truncated.")
    nonce, tag, cipher = data[:16], data[16:48], data[48:]
    key = _key(secret)
    expect = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expect):
        raise ValueError("Could not read that payload.")
    stream = hashlib.shake_256(key + nonce).digest(len(cipher))
    return bytes(a ^ b for a, b in zip(cipher, stream))


def encrypt_json(secret: str, payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
    return encrypt_bytes(secret, raw)


def decrypt_json(secret: str, blob: str) -> dict:
    raw = decrypt_bytes(secret, blob)
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("That payload is not an object.")
    return payload
