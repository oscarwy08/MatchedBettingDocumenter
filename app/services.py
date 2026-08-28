from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.calculator import money
from app.models import (
    Account,
    AccountType,
    Bet,
    BetStatus,
    Offer,
    Transfer,
    TransferKind,
)

ZERO = Decimal("0.00")


def _sum(values) -> Decimal:
    total = ZERO
    for value in values:
        if value is not None:
            total += Decimal(str(value))
    return money(total)


def transfers_for(session: Session, account_id: int) -> list[Transfer]:
    return list(
        session.scalars(
            select(Transfer).where(Transfer.account_id == account_id)
        )
    )


def deposited(session: Session, account_id: int) -> Decimal:
    rows = transfers_for(session, account_id)
    return _sum(t.amount for t in rows if t.kind == TransferKind.DEPOSIT)


def withdrawn(session: Session, account_id: int) -> Decimal:
    rows = transfers_for(session, account_id)
    return _sum(t.amount for t in rows if t.kind == TransferKind.WITHDRAWAL)


def opening_balance(session: Session, account_id: int) -> Decimal:
    rows = transfers_for(session, account_id)
    return _sum(t.amount for t in rows if t.kind == TransferKind.OPENING)


def account_snapshot(session: Session, account: Account) -> dict:
    opening = opening_balance(session, account.id)
    dep = deposited(session, account.id)
    wd = withdrawn(session, account.id)

    if account.type == AccountType.BOOKIE:
        settled = [
            bet
            for bet in session.scalars(
                select(Bet).where(
                    Bet.bookie_id == account.id,
                    Bet.status != BetStatus.PENDING,
                )
            )
        ]
        bookie_pl = _sum(bet.actual_bookie_profit for bet in settled)
        exchange_pl = _sum(bet.actual_exchange_profit for bet in settled)
        net = _sum(bet.actual_profit for bet in settled)
        balance = money(opening + dep - wd + bookie_pl)
    else:
        settled = [
            bet
            for bet in session.scalars(
                select(Bet).where(
                    Bet.exchange_id == account.id,
                    Bet.status != BetStatus.PENDING,
                )
            )
        ]
        bookie_pl = ZERO
        exchange_pl = _sum(bet.actual_exchange_profit for bet in settled)
        net = exchange_pl
        balance = money(opening + dep - wd + exchange_pl)

    return {
        "account": account,
        "opening": opening,
        "deposited": dep,
        "withdrawn": wd,
        "bookie_profit": bookie_pl,
        "exchange_profit": exchange_pl,
        "net_profit": net,
        "balance": balance,
    }


def offer_snapshot(offer: Offer) -> dict:
    settled = [bet for bet in offer.bets if bet.status != BetStatus.PENDING]
    pending = [bet for bet in offer.bets if bet.status == BetStatus.PENDING]
    bookie_pl = _sum(bet.actual_bookie_profit for bet in settled)
    exchange_pl = _sum(bet.actual_exchange_profit for bet in settled)
    net = _sum(bet.actual_profit for bet in settled)
    expected_pending = _sum(bet.expected_profit for bet in pending)
    return {
        "offer": offer,
        "status": offer.status,
        "deposited": money(offer.deposit_amount or ZERO),
        "free_funds": money(offer.free_funds or ZERO),
        "free_funds_used": money(offer.free_funds_used),
        "bookie_profit": bookie_pl,
        "exchange_profit": exchange_pl,
        "net_profit": net,
        "expected_pending": expected_pending,
        "pending_count": len(pending),
        "leg_count": len(offer.bets),
    }


def dashboard_stats(session: Session) -> dict:
    bets = list(session.scalars(select(Bet)))
    settled = [bet for bet in bets if bet.status != BetStatus.PENDING]
    pending = [bet for bet in bets if bet.status == BetStatus.PENDING]
    accounts = list(session.scalars(select(Account).order_by(Account.name)))
    snapshots = [account_snapshot(session, account) for account in accounts]

    today = date.today()
    month_profit = ZERO
    for bet in settled:
        when = bet.settled_at.date() if bet.settled_at else bet.date_placed
        if when.year == today.year and when.month == today.month:
            month_profit += bet.actual_profit or ZERO

    active_accounts = [
        snap
        for snap in snapshots
        if snap["deposited"] or snap["withdrawn"] or snap["net_profit"] or snap["balance"]
    ]

    offers = list(
        session.scalars(
            select(Offer).options(selectinload(Offer.bets)).order_by(Offer.created_at.desc())
        )
    )
    in_progress = [offer for offer in offers if offer.status == "In progress"]

    return {
        "net_profit": _sum(bet.actual_profit for bet in settled),
        "pending_expected": _sum(bet.expected_profit for bet in pending),
        "bankroll": _sum(snap["balance"] for snap in snapshots),
        "open_liability": _sum(bet.liability for bet in pending),
        "month_profit": money(month_profit),
        "pending_count": len(pending),
        "settled_count": len(settled),
        "account_snapshots": snapshots,
        "active_accounts": active_accounts,
        "profit_by_bookie": [
            snap
            for snap in snapshots
            if snap["account"].type == AccountType.BOOKIE and snap["net_profit"] != ZERO
        ],
        "in_progress_offers": in_progress,
        "pending_bets": sorted(pending, key=lambda bet: bet.date_placed, reverse=True),
        "last_synced": datetime.now(),
    }
