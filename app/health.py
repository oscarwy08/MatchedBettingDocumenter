"""Mug-bet hygiene and the daily check list, computed from the live log."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.dates import format_uk
from app.models import Account, AccountTask, AccountType, Bet, BetType, Offer, OfferType
from app.settings import load as load_settings

PROMO_TYPES = {
    BetType.QUALIFYING,
    BetType.FREE_BET_SNR,
    BetType.FREE_BET_SR,
    BetType.MONEY_BACK,
}
FREE_BET_TYPES = {BetType.FREE_BET_SNR, BetType.FREE_BET_SR}

WEEKDAYS = (
    (0, "Monday"),
    (1, "Tuesday"),
    (2, "Wednesday"),
    (3, "Thursday"),
    (4, "Friday"),
    (5, "Saturday"),
    (6, "Sunday"),
)


def _settings(overrides: dict | None = None) -> dict:
    values = load_settings()
    if overrides:
        values = {**values, **overrides}
    return values


def _reload_due(offer: Offer, today: date) -> bool:
    if not offer.repeats:
        return False
    if offer.next_reload_on is None:
        return True
    return offer.next_reload_on <= today


def mug_health(
    account: Account,
    bets: list[Bet],
    *,
    threshold: int = 4,
    today: date | None = None,
) -> dict:
    today = today or date.today()
    bookie_bets = [bet for bet in bets if bet.bookie_id == account.id]
    mugs = [bet for bet in bookie_bets if bet.bet_type == BetType.MUG]
    last_mug_on = max((bet.date_placed for bet in mugs), default=None)
    after = [
        bet
        for bet in bookie_bets
        if last_mug_on is None or bet.date_placed > last_mug_on
    ]
    qualifiers = sum(1 for bet in after if bet.bet_type == BetType.QUALIFYING)
    free_bets = sum(1 for bet in after if bet.bet_type in FREE_BET_TYPES)
    promo_since = sum(1 for bet in after if bet.bet_type in PROMO_TYPES)
    reload_since = sum(
        1
        for bet in after
        if bet.offer is not None and bet.offer.type == OfferType.RELOAD
    )
    mug_count = len(mugs)
    if promo_since == 0:
        level = "green"
    elif promo_since >= threshold:
        level = "red"
    elif promo_since >= max(threshold - 1, 1):
        level = "amber"
    else:
        level = "green"
    if level == "green":
        label = "Healthy"
    else:
        noun = "qualifier" if promo_since == 1 else "qualifiers"
        mug_noun = "mug" if mug_count == 1 else "mugs"
        label = f"{promo_since} {noun} · {mug_count} {mug_noun}"
    limit = max(int(threshold), 1)
    percent = int(round(100 * max(0, limit - promo_since) / limit))
    return {
        "account_id": account.id,
        "level": level,
        "label": label,
        "percent": percent,
        "qualifiers": qualifiers,
        "free_bets": free_bets,
        "promo_since": promo_since,
        "mugs": mug_count,
        "last_mug_on": last_mug_on,
        "reload_since": reload_since,
        "checked_today": account.last_checked_on == today,
        "last_checked_on": account.last_checked_on,
    }


def _offer_open(offer: Offer, today: date) -> bool:
    if _reload_due(offer, today):
        return True
    return offer.status == "In progress"


def _bookie_in_use(bets: list[Bet], offers: list[Offer], today: date) -> bool:
    if bets:
        return True
    return any(_offer_open(offer, today) for offer in offers)


def _days_since_check(account: Account, today: date) -> int:
    if account.last_checked_on is None:
        return 10_000
    return (today - account.last_checked_on).days


def _routine_bucket(row: dict, today: date, check_every: int, priority_days: int) -> int:
    account: Account = row["account"]
    if row["reload_due"] or row["tasks_due"]:
        return 0
    overdue_days = priority_days if account.priority else check_every
    overdue = _days_since_check(account, today) >= overdue_days
    if row["health"]["level"] == "red" or (account.priority and overdue):
        return 1
    weekday_due = account.check_weekday is not None and account.check_weekday == today.weekday()
    if overdue or weekday_due:
        return 2
    return 3


def _sort_key(row: dict, today: date, check_every: int, priority_days: int) -> tuple:
    account: Account = row["account"]
    last = account.last_checked_on or date.min
    return (
        _routine_bucket(row, today, check_every, priority_days),
        last,
        account.name.lower(),
    )


def today_board(
    session: Session,
    *,
    today: date | None = None,
    settings: dict | None = None,
) -> dict:
    today = today or date.today()
    cfg = _settings(settings)
    threshold = int(cfg["mug_after_offers"])
    check_every = int(cfg["check_every_days"])
    target = int(cfg["daily_check_target"])
    priority_days = int(cfg["priority_check_days"])

    bookies = list(
        session.scalars(
            select(Account).where(Account.type == AccountType.BOOKIE).order_by(Account.name)
        )
    )
    bets = list(session.scalars(select(Bet).options(selectinload(Bet.offer))))
    offers = list(
        session.scalars(select(Offer).options(selectinload(Offer.bookie), selectinload(Offer.bets)))
    )
    tasks = list(
        session.scalars(
            select(AccountTask)
            .options(selectinload(AccountTask.account))
            .order_by(AccountTask.due_on, AccountTask.id)
        )
    )

    bets_by_bookie: dict[int, list[Bet]] = defaultdict(list)
    for bet in bets:
        bets_by_bookie[bet.bookie_id].append(bet)
    offers_by_bookie: dict[int, list[Offer]] = defaultdict(list)
    for offer in offers:
        offers_by_bookie[offer.bookie_id].append(offer)
    tasks_by_bookie: dict[int, list[AccountTask]] = defaultdict(list)
    for task in tasks:
        tasks_by_bookie[task.account_id].append(task)

    rows = []
    used_bookies = []
    for account in bookies:
        health = mug_health(account, bets_by_bookie[account.id], threshold=threshold, today=today)
        due_offers = [offer for offer in offers_by_bookie[account.id] if _reload_due(offer, today)]
        due_tasks = [
            task
            for task in tasks_by_bookie[account.id]
            if not task.done and task.due_on <= today
        ]
        in_use = _bookie_in_use(bets_by_bookie[account.id], offers_by_bookie[account.id], today)
        if in_use:
            used_bookies.append(account)
        rows.append(
            {
                "account": account,
                "health": health,
                "checked_today": account.last_checked_on == today,
                "reload_due": due_offers,
                "tasks_due": due_tasks,
                "open_tasks": [task for task in tasks_by_bookie[account.id] if not task.done],
                "in_use": in_use,
            }
        )
    health_by_id = {row["account"].id: row["health"] for row in rows}
    rows = [row for row in rows if row["in_use"] or row["tasks_due"]]

    ticked = [row for row in rows if row["checked_today"]]
    pending = [row for row in rows if not row["checked_today"]]
    pending.sort(key=lambda row: _sort_key(row, today, check_every, priority_days))
    slots = max(target - len(ticked), 0)
    routine = pending[:slots] + ticked
    routine.sort(
        key=lambda row: (
            row["checked_today"],
            _sort_key(row, today, check_every, priority_days),
        )
    )

    specials: list[dict] = []
    for offer in offers:
        if _reload_due(offer, today):
            specials.append(
                {
                    "kind": "reload",
                    "account": offer.bookie,
                    "offer": offer,
                    "task": None,
                    "name": offer.name,
                    "detail": offer.next_reload_on,
                }
            )
    for task in tasks:
        if not task.done and task.due_on <= today:
            specials.append(
                {
                    "kind": "task",
                    "account": task.account,
                    "offer": None,
                    "task": task,
                    "name": task.note or "Check this bookie",
                    "detail": task.due_on,
                }
            )
    specials.sort(key=lambda item: (item["account"].name.lower() if item["account"] else "", item["name"]))

    week_start = today - timedelta(days=today.weekday())
    checked_dates = {account.last_checked_on for account in bookies if account.last_checked_on}
    week = []
    for offset in range(7):
        day = week_start + timedelta(days=offset)
        week.append(
            {
                "date": day,
                "label": day.strftime("%a"),
                "count": sum(1 for account in used_bookies if account.last_checked_on == day),
                "today": day == today,
                "future": day > date.today(),
                "href": format_uk(day),
            }
        )

    checked_count = len(ticked)
    return {
        "today": today,
        "target": target,
        "checked_count": checked_count,
        "routine": routine,
        "specials": specials,
        "week": week,
        "clean": not pending[:slots] and not specials,
        "health_by_id": health_by_id,
        "checked_dates": checked_dates,
    }


def account_health(session: Session, account: Account, *, today: date | None = None) -> dict:
    today = today or date.today()
    if not account.is_bookie:
        return {
            "level": "green",
            "label": "Exchange",
            "percent": 100,
            "qualifiers": 0,
            "free_bets": 0,
            "promo_since": 0,
            "mugs": 0,
            "last_mug_on": None,
            "reload_since": 0,
            "checked_today": False,
            "last_checked_on": None,
        }
    bets = list(
        session.scalars(
            select(Bet).options(selectinload(Bet.offer)).where(Bet.bookie_id == account.id)
        )
    )
    return mug_health(account, bets, threshold=int(_settings()["mug_after_offers"]), today=today)


def attach_health(snapshots: list[dict], health_by_id: dict[int, dict]) -> None:
    blank = {
        "level": "green",
        "label": "Healthy",
        "percent": 100,
        "qualifiers": 0,
        "mugs": 0,
        "promo_since": 0,
        "last_mug_on": None,
        "checked_today": False,
    }
    for snap in snapshots:
        account = snap["account"]
        snap["health"] = health_by_id.get(account.id, blank) if account.is_bookie else None
