from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.calculator import money
from app.models import (
    Account,
    AccountType,
    Bet,
    BetStatus,
    BetType,
    Offer,
    Transfer,
    TransferKind,
)

ZERO = Decimal("0.00")
_SIDES_TOLERANCE = Decimal("0.02")


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


def suggested_settlement(bet: Bet) -> dict:
    return {
        BetStatus.BACK_WON: {
            "bookie": bet.expected_bookie_back,
            "exchange": bet.expected_exchange_back,
            "net": bet.expected_bookie_back + bet.expected_exchange_back,
        },
        BetStatus.LAY_WON: {
            "bookie": bet.expected_bookie_lay,
            "exchange": bet.expected_exchange_lay,
            "net": bet.expected_bookie_lay + bet.expected_exchange_lay,
        },
        BetStatus.VOID: {
            "bookie": ZERO,
            "exchange": ZERO,
            "net": ZERO,
        },
    }


def _bookie_for_outcome(bet: Bet, status: str) -> Decimal:
    """Bookie cash from the outcome, ignoring stored expected sides.

    Those sides can be wrong when a free bet was logged with no lay.
    """
    if status == BetStatus.VOID:
        return ZERO
    stake = Decimal(str(bet.back_stake or ZERO))
    odds = Decimal(str(bet.back_odds or ZERO))
    extra = Decimal(str(bet.cashback or ZERO))
    if status == BetStatus.LAY_WON:
        if bet.bet_type == BetType.FREE_BET_SNR:
            return ZERO
        if bet.bet_type == BetType.MONEY_BACK:
            return money(-stake + extra)
        return money(-stake)
    return money(stake * (odds - 1)) if odds else ZERO


def settlement_amounts(bet: Bet) -> dict:
    """Bookie / exchange / net used for totals.

    Trust stored sides only when they add up to the stored net. Otherwise the
    outcome decides the bookie figure (a lost free bet is £0 at the bookie)
    and the rest of the net sits on the exchange. Unmatched legs (no lay)
    put the whole net on the bookie.
    """
    if bet.status == BetStatus.PENDING:
        return {"bookie": ZERO, "exchange": ZERO, "net": ZERO}
    suggested = suggested_settlement(bet).get(bet.status) or {
        "bookie": ZERO,
        "exchange": ZERO,
        "net": ZERO,
    }
    net = bet.actual_profit if bet.actual_profit is not None else suggested["net"]
    bookie = bet.actual_bookie_profit
    exchange = bet.actual_exchange_profit
    if bookie is not None and exchange is not None:
        if abs(money(bookie + exchange) - money(net)) <= _SIDES_TOLERANCE:
            return {
                "bookie": money(bookie),
                "exchange": money(exchange),
                "net": money(net),
            }
    if money(bet.lay_stake or ZERO) == ZERO:
        return {"bookie": money(net), "exchange": ZERO, "net": money(net)}
    bookie = _bookie_for_outcome(bet, bet.status)
    return {
        "bookie": bookie,
        "exchange": money(net - bookie),
        "net": money(net),
    }


def reconcile_settlement_sides(session: Session) -> int:
    """Rewrite stored bookie/exchange P&L when they do not add up to net."""
    changes = 0
    bets = list(session.scalars(select(Bet).where(Bet.status != BetStatus.PENDING)))
    for bet in bets:
        amounts = settlement_amounts(bet)
        if (
            bet.actual_bookie_profit != amounts["bookie"]
            or bet.actual_exchange_profit != amounts["exchange"]
            or bet.actual_profit != amounts["net"]
        ):
            bet.actual_bookie_profit = amounts["bookie"]
            bet.actual_exchange_profit = amounts["exchange"]
            bet.actual_profit = amounts["net"]
            changes += 1
    return changes


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
        amounts = [settlement_amounts(bet) for bet in settled]
        bookie_pl = _sum(row["bookie"] for row in amounts)
        exchange_pl = _sum(row["exchange"] for row in amounts)
        net = _sum(row["net"] for row in amounts)
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
        amounts = [settlement_amounts(bet) for bet in settled]
        bookie_pl = ZERO
        exchange_pl = _sum(row["exchange"] for row in amounts)
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
    amounts = [settlement_amounts(bet) for bet in settled]
    bookie_pl = _sum(row["bookie"] for row in amounts)
    exchange_pl = _sum(row["exchange"] for row in amounts)
    net = _sum(row["net"] for row in amounts)
    expected_pending = _sum(bet.expected_profit for bet in pending)
    return {
        "offer": offer,
        "status": offer.status,
        "deposited": money(offer.deposit_amount or ZERO),
        # deposit_amount is kept in sync with a single Transfer; see sync_offer_deposit.
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


def _offer_deposit_transfers(session: Session, offer_id: int) -> list[Transfer]:
    return list(
        session.scalars(
            select(Transfer)
            .where(
                Transfer.offer_id == offer_id,
                Transfer.kind == TransferKind.DEPOSIT,
            )
            .order_by(Transfer.id)
        )
    )


def _looks_like_duplicate_deposit(transfer: Transfer, offer: Offer) -> bool:
    notes = (transfer.notes or "").strip().lower()
    name = (offer.name or "").strip().lower()
    if not notes:
        return True
    if name and name in notes:
        return True
    return notes.startswith("deposit for")


def sync_offer_deposit(session: Session, offer: Offer, when: date | None = None) -> bool:
    """Keep exactly one deposit transfer for an offer's bookie deposit."""
    amount = money(offer.deposit_amount or ZERO)
    linked = _offer_deposit_transfers(session, offer.id)
    changed = False

    if amount <= 0:
        for transfer in linked:
            session.delete(transfer)
            changed = True
        return changed

    if linked:
        primary, *extras = linked
        if primary.amount != amount or primary.account_id != offer.bookie_id:
            primary.amount = amount
            primary.account_id = offer.bookie_id
            changed = True
        if not (primary.notes or "").strip():
            primary.notes = f"Deposit for {offer.name}"
            changed = True
        for extra in extras:
            session.delete(extra)
            changed = True
        return changed

    match = session.scalars(
        select(Transfer)
        .where(
            Transfer.account_id == offer.bookie_id,
            Transfer.kind == TransferKind.DEPOSIT,
            Transfer.offer_id.is_(None),
            Transfer.amount == amount,
        )
        .order_by(Transfer.id)
    ).first()
    if match:
        match.offer_id = offer.id
        if not (match.notes or "").strip():
            match.notes = f"Deposit for {offer.name}"
        return True

    session.add(
        Transfer(
            account_id=offer.bookie_id,
            kind=TransferKind.DEPOSIT,
            amount=amount,
            date=when or date.today(),
            notes=f"Deposit for {offer.name}",
            offer_id=offer.id,
        )
    )
    return True


def reconcile_offer_deposits(session: Session) -> int:
    """Create missing offer transfers and drop obvious double-counted deposits."""
    changes = 0
    offers = list(session.scalars(select(Offer)))
    for offer in offers:
        if sync_offer_deposit(session, offer):
            changes += 1

    linked = list(
        session.scalars(
            select(Transfer).where(
                Transfer.kind == TransferKind.DEPOSIT,
                Transfer.offer_id.is_not(None),
            )
        )
    )
    for transfer in linked:
        offer = transfer.offer or session.get(Offer, transfer.offer_id)
        if offer is None:
            continue
        twins = list(
            session.scalars(
                select(Transfer).where(
                    Transfer.kind == TransferKind.DEPOSIT,
                    Transfer.account_id == transfer.account_id,
                    Transfer.amount == transfer.amount,
                    Transfer.offer_id.is_(None),
                )
            )
        )
        for twin in twins:
            if _looks_like_duplicate_deposit(twin, offer):
                session.delete(twin)
                changes += 1
    return changes


def account_usage(session: Session, account_id: int) -> dict:
    bets = session.scalar(
        select(func.count())
        .select_from(Bet)
        .where(or_(Bet.bookie_id == account_id, Bet.exchange_id == account_id))
    ) or 0
    offers = session.scalar(
        select(func.count()).select_from(Offer).where(Offer.bookie_id == account_id)
    ) or 0
    transfers = session.scalar(
        select(func.count()).select_from(Transfer).where(Transfer.account_id == account_id)
    ) or 0
    return {
        "bets": int(bets),
        "offers": int(offers),
        "transfers": int(transfers),
        "can_delete": bets == 0 and offers == 0 and transfers == 0,
    }
