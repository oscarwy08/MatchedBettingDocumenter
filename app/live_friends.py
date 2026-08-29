"""Publish encrypted friend views on the mailbox when an invite is active."""

from __future__ import annotations

import threading

from app.friends import encrypt_view, has_active_invite, load_state, view_dto
from app.mailbox import put
from app.sync import default_nickname

PUBLISH_EVERY_SEC = 20

_started = False
_wakeup = threading.Event()


def _session():
    from app.db import SessionLocal

    if SessionLocal is None:
        raise RuntimeError("Database is not initialised.")
    return SessionLocal()


def publish_now() -> int:
    if not has_active_invite():
        return 0
    session = _session()
    posted = 0
    try:
        from app.friends import account_name

        dto = view_dto(session, nickname=account_name() or default_nickname())
        for invite in load_state().get("invites") or []:
            secret = invite.get("secret")
            if not secret:
                continue
            put("view", secret, encrypt_view(secret, dto))
            posted += 1
    finally:
        session.close()
    return posted


def notify() -> None:
    _wakeup.set()


def _loop() -> None:
    while True:
        try:
            publish_now()
        except Exception:  # noqa: BLE001
            pass
        _wakeup.wait(timeout=PUBLISH_EVERY_SEC)
        _wakeup.clear()


def start_background() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="mbd-friend-mailbox", daemon=True).start()
