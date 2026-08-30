"""LAN address shown in the Open on phone QR."""

from __future__ import annotations


def _listen_port(fallback: int) -> int:
    try:
        from flask import current_app, has_app_context, has_request_context, request
    except ImportError:
        return fallback
    if has_app_context():
        bound = current_app.config.get("BIND_PORT")
        if bound:
            return int(bound)
    if has_request_context():
        try:
            return int(request.environ.get("SERVER_PORT") or fallback)
        except (TypeError, ValueError):
            return fallback
    return fallback


def _lan_is_bound() -> bool:
    try:
        from flask import current_app, has_app_context
    except ImportError:
        return False
    if has_app_context():
        host = current_app.config.get("BIND_HOST")
        if host:
            return host != "127.0.0.1"
    from app.settings import get as setting

    return bool(setting("allow_lan"))


def phone_context() -> dict:
    from app.nat import lan_ip
    from app.qr import qr_svg
    from app.settings import get as setting

    port = _listen_port(int(setting("port")))
    ip = lan_ip()
    url = f"http://{ip}:{port}"
    ready = _lan_is_bound() and bool(ip) and not str(ip).startswith("127.")
    return {
        "phone_url": url,
        "phone_ready": ready,
        "phone_qr_svg": qr_svg(url) if ready else "",
    }
