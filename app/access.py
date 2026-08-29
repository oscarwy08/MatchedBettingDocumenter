"""Who may hit this process: this computer / Wi‑Fi get the UI; the internet only gets tokened APIs."""

from __future__ import annotations

import ipaddress
import time

from flask import abort, request

RATE_WINDOW_SEC = 60
RATE_MAX = 30
_rate: dict[str, list[float]] = {}

# Durable token APIs a paired computer or friend may call from outside the LAN.
_REMOTE_PATHS = {
    "/api/sync/status",
    "/api/sync/snapshot",
    "/api/sync/hello",
    "/api/friend/view",
}


def client_ip() -> str:
    # Do not trust X-Forwarded-For — this app is not behind a login proxy.
    return (request.remote_addr or "").strip()


def is_trusted_client(ip: str | None = None) -> bool:
    raw = (ip if ip is not None else client_ip()) or ""
    try:
        addr = ipaddress.ip_address(raw.split("%")[0])
    except ValueError:
        return False
    return bool(addr.is_loopback or addr.is_private or addr.is_link_local)


def remote_api_allowed(path: str) -> bool:
    if path in _REMOTE_PATHS:
        return True
    if path.startswith("/api/sync/"):
        pin = path[len("/api/sync/") :]
        return pin.isdigit() and len(pin) == 6
    return False


def allow_rate(ip: str, *, limit: int = RATE_MAX) -> bool:
    now = time.time()
    hits = [stamp for stamp in _rate.get(ip, []) if now - stamp < RATE_WINDOW_SEC]
    if len(hits) >= limit:
        _rate[ip] = hits
        return False
    hits.append(now)
    _rate[ip] = hits
    return True


def enforce_local_ui():
    if is_trusted_client():
        return None
    if not remote_api_allowed(request.path):
        abort(403)
    if not allow_rate(client_ip() or "wan"):
        abort(429)
    return None
