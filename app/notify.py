"""Create one notification per due bet start and Today/calendar item."""

from __future__ import annotations

import threading
import time

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.dates import format_uk_time, local_now
from app.health import site_scan
from app.models import (
    AccountTask,
    Bet,
    BetStatus,
    Notification,
    Offer,
    ScheduleEvent,
)

SWEEP_EVERY_SEC = 20

_started = False


def _session() -> Session:
    from app.db import SessionLocal

    if SessionLocal is None:
        raise RuntimeError("Database is not initialised.")
    return SessionLocal()


def as_dict(row: Notification) -> dict:
    return {
        "id": row.id,
        "kind": row.kind,
        "title": row.title,
        "body": row.body,
        "href": row.href,
        "unread": row.read_at is None,
        "created": format_uk_time(row.created_at),
    }


def list_notifications(session: Session, *, limit: int | None = None) -> list[Notification]:
    query = select(Notification).order_by(Notification.id.desc())
    if limit is not None:
        query = query.limit(limit)
    return list(session.scalars(query))


def unread_count(session: Session) -> int:
    return len(list(session.scalars(select(Notification).where(Notification.read_at.is_(None)))))


def mark_read(session: Session, notification_id: int | None = None, *, all_items: bool = False) -> int:
    now = local_now()
    rows = list_notifications(session)
    changed = 0
    for row in rows:
        if row.read_at is not None:
            continue
        if all_items or row.id == notification_id:
            row.read_at = now
            changed += 1
    if changed:
        session.commit()
    return changed


def sweep(session: Session, *, now=None, send_desktop: bool = True) -> list[Notification]:
    clock = now or local_now()
    today = clock.date() if hasattr(clock, "hour") else clock
    _refresh_fixtures(session, clock)
    existing = set(session.scalars(select(Notification.source_key)))
    created: list[Notification] = []
    for payload in _due(session, clock, today):
        if payload["source_key"] in existing:
            continue
        row = Notification(**payload)
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            continue
        existing.add(row.source_key)
        created.append(row)
    if created and send_desktop:
        _desktop(created)
    return created


def start_background() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="mbd-notify", daemon=True).start()


def _loop() -> None:
    while True:
        try:
            session = _session()
            try:
                sweep(session, send_desktop=True)
            finally:
                session.close()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(SWEEP_EVERY_SEC)


def _refresh_fixtures(session: Session, clock) -> None:
    from datetime import datetime, time

    from app.fixtures import refresh

    when = clock if hasattr(clock, "hour") else datetime.combine(clock, time.max)
    pending = list(
        session.scalars(
            select(Bet).where(
                Bet.status == BetStatus.PENDING,
                Bet.fixture_id.is_not(None),
            )
        )
    )
    football_live = False
    racing_results = False
    for bet in pending:
        ident = (bet.fixture_id or "").strip()
        if not ident:
            continue
        started = bet.starts_at is None or bet.starts_at <= when
        if bet.fixture_source == "football" and started:
            football_live = True
        if bet.fixture_source == "racing" and started:
            racing_results = True
    try:
        refresh(now=when if hasattr(when, "hour") else None, football_live=football_live, racing_results=racing_results)
    except Exception:  # noqa: BLE001
        pass


def _desktop(rows: list[Notification]) -> None:
    from app.settings import get as setting

    if not setting("desktop_notifications"):
        return
    from app.desktop_notify import send

    for row in rows:
        try:
            send(row.title, row.body)
        except Exception:  # noqa: BLE001
            pass


def _due(session: Session, clock, today) -> list[dict]:
    from datetime import datetime, time

    when = clock if hasattr(clock, "hour") else datetime.combine(clock, time.max)
    day = today.isoformat()
    items: list[dict] = []
    bets = list(
        session.scalars(
            select(Bet)
            .options(selectinload(Bet.bookie))
            .where(Bet.status == BetStatus.PENDING, Bet.starts_at.is_not(None))
        )
    )
    for bet in bets:
        if bet.starts_at is None or bet.starts_at > when:
            continue
        bookie = bet.bookie.name if bet.bookie is not None else ""
        event = bet.event or "Untitled"
        items.append(
            {
                "kind": "bet_started",
                "title": "Bet started",
                "body": f"{event} · {bookie}. Starts {format_uk_time(bet.starts_at)}. Open to settle.",
                "href": f"/bets/{bet.id}",
                "source_key": f"bet:{bet.id}:starts",
            }
        )
    from app.fixtures import is_finished

    linked = list(
        session.scalars(
            select(Bet)
            .options(selectinload(Bet.bookie))
            .where(Bet.status == BetStatus.PENDING, Bet.fixture_id.is_not(None))
        )
    )
    for bet in linked:
        ident = (bet.fixture_id or "").strip()
        if not ident or not is_finished(bet.fixture_source, ident):
            continue
        bookie = bet.bookie.name if bet.bookie is not None else ""
        event = bet.event or "Untitled"
        items.append(
            {
                "kind": "bet_ended",
                "title": "Event finished",
                "body": f"{event} · {bookie}. Open to settle.",
                "href": f"/bets/{bet.id}",
                "source_key": f"bet:{bet.id}:ends",
            }
        )
    offers = list(session.scalars(select(Offer).options(selectinload(Offer.bookie))))
    for offer in offers:
        if not offer.repeats:
            continue
        if offer.next_reload_on is not None and offer.next_reload_on > today:
            continue
        bookie = offer.bookie.name if offer.bookie is not None else ""
        items.append(
            {
                "kind": "reload_due",
                "title": "Reload due",
                "body": f"{offer.name} · {bookie}. Due today. Open the offer.",
                "href": f"/offers/{offer.id}",
                "source_key": f"reload:{offer.id}:{day}",
            }
        )
    tasks = list(
        session.scalars(
            select(AccountTask)
            .options(selectinload(AccountTask.account))
            .where(AccountTask.done.is_(False), AccountTask.due_on <= today)
        )
    )
    for task in tasks:
        bookie = task.account.name if task.account is not None else ""
        note = task.note or "Check this bookie"
        items.append(
            {
                "kind": "task_due",
                "title": "Check due",
                "body": f"{note} · {bookie}. Due today.",
                "href": f"/accounts/{task.account_id}",
                "source_key": f"task:{task.id}:{day}",
            }
        )
    events = list(
        session.scalars(
            select(ScheduleEvent).where(ScheduleEvent.done.is_(False), ScheduleEvent.due_on <= today)
        )
    )
    for event in events:
        items.append(
            {
                "kind": "event_due",
                "title": "Personal offer",
                "body": f"{event.title}. On Today.",
                "href": "/today",
                "source_key": f"event:{event.id}:{day}",
            }
        )
    scan = site_scan(today)
    if scan.get("due"):
        items.append(
            {
                "kind": "site_scan",
                "title": "Check sites",
                "body": "Look for new offers. On Today.",
                "href": "/today",
                "source_key": f"scan:{day}",
            }
        )
    return items
