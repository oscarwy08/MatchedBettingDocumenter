"""Full-database snapshot for device linking and backups."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.models import Account, AccountTask, Bet, Offer, ScheduleEvent, Transfer
from app.version import VERSION

SNAPSHOT_FORMAT = 2


def _jsonable(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    return value


def _row(model, fields: list[str]) -> dict:
    return {name: _jsonable(getattr(model, name)) for name in fields}


ACCOUNT_FIELDS = [
    "id",
    "name",
    "type",
    "commission_percent",
    "last_checked_on",
    "priority",
    "restriction",
    "notes",
    "check_weekday",
    "created_at",
]
TASK_FIELDS = ["id", "account_id", "due_on", "note", "done", "created_at"]
EVENT_FIELDS = ["id", "title", "due_on", "bookie_id", "notes", "repeat", "done", "created_at"]
OFFER_FIELDS = [
    "id",
    "name",
    "type",
    "bookie_id",
    "deposit_amount",
    "free_funds",
    "reload_frequency",
    "reload_stake",
    "reload_reward",
    "next_reload_on",
    "notes",
    "created_at",
]
BET_FIELDS = [
    "id",
    "offer_id",
    "date_placed",
    "event",
    "market",
    "notes",
    "bet_type",
    "bookie_id",
    "exchange_id",
    "back_stake",
    "back_odds",
    "lay_stake",
    "lay_odds",
    "commission_percent",
    "cashback",
    "liability",
    "expected_profit",
    "expected_bookie_back",
    "expected_exchange_back",
    "expected_bookie_lay",
    "expected_exchange_lay",
    "status",
    "actual_profit",
    "actual_bookie_profit",
    "actual_exchange_profit",
    "placed_at",
    "starts_at",
    "ends_at",
    "fixture_source",
    "fixture_id",
    "settled_at",
    "free_bet_returned",
    "created_at",
]
TRANSFER_FIELDS = ["id", "account_id", "kind", "amount", "date", "notes", "offer_id", "created_at"]


def dump_snapshot(session: Session) -> dict:
    payload = {
        "format": SNAPSHOT_FORMAT,
        "app_version": VERSION,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "accounts": [_row(item, ACCOUNT_FIELDS) for item in session.scalars(select(Account).order_by(Account.id))],
        "offers": [_row(item, OFFER_FIELDS) for item in session.scalars(select(Offer).order_by(Offer.id))],
        "bets": [_row(item, BET_FIELDS) for item in session.scalars(select(Bet).order_by(Bet.id))],
        "transfers": [
            _row(item, TRANSFER_FIELDS) for item in session.scalars(select(Transfer).order_by(Transfer.id))
        ],
        "account_tasks": [
            _row(item, TASK_FIELDS) for item in session.scalars(select(AccountTask).order_by(AccountTask.id))
        ],
        "schedule_events": [
            _row(item, EVENT_FIELDS) for item in session.scalars(select(ScheduleEvent).order_by(ScheduleEvent.id))
        ],
    }
    from app.friends import export_account

    payload["friends"] = export_account()
    payload["counts"] = snapshot_counts(payload)
    payload["fingerprint"] = fingerprint_payload(payload)
    return payload


def _count_field(value) -> int:
    if isinstance(value, list):
        return len(value)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def snapshot_counts(payload: dict) -> dict:
    return {
        "accounts": _count_field(payload.get("accounts")),
        "offers": _count_field(payload.get("offers")),
        "bets": _count_field(payload.get("bets")),
        "transfers": _count_field(payload.get("transfers")),
        "account_tasks": _count_field(payload.get("account_tasks")),
        "schedule_events": _count_field(payload.get("schedule_events")),
    }


def _friends_body(payload: dict) -> dict:
    friends = payload.get("friends")
    if not isinstance(friends, dict):
        return {"account_name": "", "invites": [], "friends": []}
    return {
        "account_name": friends.get("account_name") or "",
        "invites": friends.get("invites") or [],
        "friends": friends.get("friends") or [],
    }


def fingerprint_bets_only(payload: dict) -> str:
    """Pre-1.6.2 hash — used to keep last_agreed after the friends field was added."""
    body = {
        "accounts": payload.get("accounts") or [],
        "offers": payload.get("offers") or [],
        "bets": payload.get("bets") or [],
        "transfers": payload.get("transfers") or [],
    }
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def fingerprint_payload(payload: dict) -> str:
    body = {
        "accounts": payload.get("accounts") or [],
        "offers": payload.get("offers") or [],
        "bets": payload.get("bets") or [],
        "transfers": payload.get("transfers") or [],
        "account_tasks": payload.get("account_tasks") or [],
        "schedule_events": payload.get("schedule_events") or [],
        "friends": _friends_body(payload),
    }
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def fingerprint_session(session: Session) -> str:
    return fingerprint_payload(dump_snapshot(session))


def would_shrink(local: dict, remote: dict) -> bool:
    local_c = snapshot_counts(local)
    remote_c = snapshot_counts(remote)
    return local_c["bets"] > remote_c["bets"] or local_c["offers"] > remote_c["offers"]


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _parse_d(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value[:10])


def _bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes"}


def _dec(value) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def apply_snapshot(session: Session, payload: dict, *, backup_why: str | None = "before-sync") -> dict:
    if not isinstance(payload, dict) or "accounts" not in payload:
        raise ValueError("That file is not a Documenter backup.")
    if backup_why:
        from app.backups import save_current

        save_current(session, why=backup_why)
    session.execute(delete(Bet))
    session.execute(delete(Transfer))
    session.execute(delete(Offer))
    session.execute(delete(AccountTask))
    session.execute(delete(ScheduleEvent))
    session.execute(delete(Account))
    session.flush()

    for row in payload.get("accounts", []):
        weekday = row.get("check_weekday")
        session.add(
            Account(
                id=int(row["id"]),
                name=row["name"],
                type=row["type"],
                commission_percent=_dec(row.get("commission_percent")) or Decimal("0"),
                last_checked_on=_parse_d(row.get("last_checked_on")),
                priority=_bool(row.get("priority")),
                restriction=row.get("restriction") or "",
                notes=row.get("notes") or "",
                check_weekday=int(weekday) if weekday not in (None, "") else None,
                created_at=_parse_dt(row.get("created_at")) or datetime.now(),
            )
        )
    session.flush()
    for row in payload.get("offers", []):
        session.add(
            Offer(
                id=int(row["id"]),
                name=row["name"],
                type=row.get("type") or "other",
                bookie_id=int(row["bookie_id"]),
                deposit_amount=_dec(row.get("deposit_amount")) or Decimal("0"),
                free_funds=_dec(row.get("free_funds")) or Decimal("0"),
                reload_frequency=row.get("reload_frequency") or "",
                reload_stake=_dec(row.get("reload_stake")) or Decimal("0"),
                reload_reward=_dec(row.get("reload_reward")) or Decimal("0"),
                next_reload_on=_parse_d(row.get("next_reload_on")),
                notes=row.get("notes") or "",
                created_at=_parse_dt(row.get("created_at")) or datetime.now(),
            )
        )
    session.flush()
    for row in payload.get("transfers", []):
        offer_id = row.get("offer_id")
        session.add(
            Transfer(
                id=int(row["id"]),
                account_id=int(row["account_id"]),
                kind=row["kind"],
                amount=_dec(row["amount"]) or Decimal("0"),
                date=_parse_d(row.get("date")) or date.today(),
                notes=row.get("notes") or "",
                offer_id=int(offer_id) if offer_id else None,
                created_at=_parse_dt(row.get("created_at")) or datetime.now(),
            )
        )
    for row in payload.get("bets", []):
        offer_id = row.get("offer_id")
        session.add(
            Bet(
                id=int(row["id"]),
                offer_id=int(offer_id) if offer_id else None,
                date_placed=_parse_d(row.get("date_placed")) or date.today(),
                placed_at=_parse_dt(row.get("placed_at")),
                starts_at=_parse_dt(row.get("starts_at")),
                ends_at=_parse_dt(row.get("ends_at")),
                fixture_source=row.get("fixture_source") or None,
                fixture_id=row.get("fixture_id") or None,
                event=row.get("event") or "",
                market=row.get("market") or "",
                notes=row.get("notes") or "",
                bet_type=row.get("bet_type") or "qualifying",
                bookie_id=int(row["bookie_id"]),
                exchange_id=int(row["exchange_id"]),
                back_stake=_dec(row["back_stake"]) or Decimal("0"),
                back_odds=_dec(row["back_odds"]) or Decimal("0"),
                lay_stake=_dec(row["lay_stake"]) or Decimal("0"),
                lay_odds=_dec(row["lay_odds"]) or Decimal("0"),
                commission_percent=_dec(row.get("commission_percent")) or Decimal("0"),
                cashback=_dec(row.get("cashback")) or Decimal("0"),
                liability=_dec(row.get("liability")) or Decimal("0"),
                expected_profit=_dec(row.get("expected_profit")) or Decimal("0"),
                expected_bookie_back=_dec(row.get("expected_bookie_back")) or Decimal("0"),
                expected_exchange_back=_dec(row.get("expected_exchange_back")) or Decimal("0"),
                expected_bookie_lay=_dec(row.get("expected_bookie_lay")) or Decimal("0"),
                expected_exchange_lay=_dec(row.get("expected_exchange_lay")) or Decimal("0"),
                status=row.get("status") or "pending",
                actual_profit=_dec(row.get("actual_profit")),
                actual_bookie_profit=_dec(row.get("actual_bookie_profit")),
                actual_exchange_profit=_dec(row.get("actual_exchange_profit")),
                settled_at=_parse_dt(row.get("settled_at")),
                free_bet_returned=_bool(row.get("free_bet_returned")),
                created_at=_parse_dt(row.get("created_at")) or datetime.now(),
            )
        )
    session.flush()
    for row in payload.get("account_tasks", []):
        session.add(
            AccountTask(
                id=int(row["id"]),
                account_id=int(row["account_id"]),
                due_on=_parse_d(row.get("due_on")) or date.today(),
                note=row.get("note") or "",
                done=_bool(row.get("done")),
                created_at=_parse_dt(row.get("created_at")) or datetime.now(),
            )
        )
    for row in payload.get("schedule_events", []):
        bookie_id = row.get("bookie_id")
        session.add(
            ScheduleEvent(
                id=int(row["id"]),
                title=row.get("title") or "Personal offer",
                due_on=_parse_d(row.get("due_on")) or date.today(),
                bookie_id=int(bookie_id) if bookie_id else None,
                notes=row.get("notes") or "",
                repeat=row.get("repeat") or "",
                done=_bool(row.get("done")),
                created_at=_parse_dt(row.get("created_at")) or datetime.now(),
            )
        )
    session.flush()
    _reset_sqlite_sequences(session, payload)
    if "friends" in payload:
        from app.friends import apply_account

        apply_account(payload.get("friends"))
    return {
        "accounts": len(payload.get("accounts", [])),
        "offers": len(payload.get("offers", [])),
        "bets": len(payload.get("bets", [])),
        "transfers": len(payload.get("transfers", [])),
    }


def _reset_sqlite_sequences(session: Session, payload: dict) -> None:
    bind = session.get_bind()
    if bind is None or bind.dialect.name != "sqlite":
        return
    exists = session.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'")
    ).first()
    if not exists:
        return
    tables = {
        "accounts": payload.get("accounts", []),
        "offers": payload.get("offers", []),
        "bets": payload.get("bets", []),
        "transfers": payload.get("transfers", []),
        "account_tasks": payload.get("account_tasks", []),
        "schedule_events": payload.get("schedule_events", []),
    }
    for table, rows in tables.items():
        max_id = max((int(row["id"]) for row in rows), default=0)
        session.execute(text("DELETE FROM sqlite_sequence WHERE name = :name"), {"name": table})
        if max_id:
            session.execute(
                text("INSERT INTO sqlite_sequence (name, seq) VALUES (:name, :seq)"),
                {"name": table, "seq": max_id},
            )
