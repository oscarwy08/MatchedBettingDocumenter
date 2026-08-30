"""Series for the dashboard graph and the Visualiser page."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.calculator import money
from app.dates import format_uk, parse_uk
from app.models import (
    Account,
    AccountType,
    Bet,
    BetStatus,
    Offer,
    Transfer,
    TransferKind,
)
from app.services import ZERO, _sum, account_snapshot, settlement_amounts

RANGES = ("1D", "1W", "1M", "1Y")
VIEWS = (
    "profit_time",
    "by_bookie",
    "by_exchange",
    "by_offer_type",
    "by_bet_type",
    "by_offer",
    "outcomes",
    "activity",
    "balances",
    "cashflow",
)

_VIEW_TITLES = {
    "profit_time": "Profit over time",
    "by_bookie": "Profit by bookie",
    "by_exchange": "Profit by exchange",
    "by_offer_type": "Profit by offer type",
    "by_bet_type": "Profit by bet type",
    "by_offer": "Profit by offer",
    "outcomes": "Outcomes",
    "activity": "Bets placed",
    "balances": "Account balances",
    "cashflow": "Deposits and withdrawals",
}

_OFFER_LABELS = {
    "welcome": "Welcome",
    "reload": "Reload",
    "risk_free": "Risk-free",
    "acca_insurance": "Acca insurance",
    "extra_place": "Extra place",
    "price_boost": "Price boost",
    "other": "Other",
}

_BET_LABELS = {
    "qualifying": "Qualifying bet",
    "free_bet_snr": "Free bet (SNR)",
    "free_bet_sr": "Free bet (SR)",
    "money_back": "Money back",
    "normal": "Normal",
    "acca": "Accumulator",
    "bet_builder": "Bet builder",
    "other": "Other",
}

_OUTCOME_LABELS = {
    BetStatus.BACK_WON: "Back won",
    BetStatus.LAY_WON: "Lay won",
    BetStatus.VOID: "Void",
    BetStatus.PENDING: "Pending",
}


def _num(value) -> float:
    return float(money(value or ZERO))


SPARK_WIDTH = 100
SPARK_HEIGHT = 42
SPARK_PAD = 3


def spark_points(
    values: list[float],
    *,
    width: float = SPARK_WIDTH,
    height: float = SPARK_HEIGHT,
    pad: float = SPARK_PAD,
) -> str:
    series = list(values) or [0.0, 0.0]
    if len(series) == 1:
        series = [series[0], series[0]]
    lo = min(series)
    hi = max(series)
    span = hi - lo
    inner_w = width - pad * 2
    inner_h = height - pad * 2
    last = max(len(series) - 1, 1)
    points = []
    for index, value in enumerate(series):
        x = pad + (index / last) * inner_w
        if span == 0:
            y = height / 2
        else:
            y = pad + (1 - (value - lo) / span) * inner_h
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)


def spark_area(
    points: str,
    *,
    height: float = SPARK_HEIGHT,
    pad: float = SPARK_PAD,
) -> str:
    bits = points.split()
    if not bits:
        return ""
    first_x = bits[0].split(",")[0]
    last_x = bits[-1].split(",")[0]
    bottom = height - pad
    return (
        f"M {bits[0]} L {' L '.join(bits[1:])} "
        f"L {last_x},{bottom:.1f} L {first_x},{bottom:.1f} Z"
    )


def spark_payload(values: list[float] | None = None) -> dict:
    series = list(values) if values else [0.0, 0.0]
    points = spark_points(series)
    return {
        "values": series,
        "points": points,
        "area": spark_area(points),
        "down": bool(series) and series[-1] < 0,
    }


def empty_spark() -> dict:
    return spark_payload([0.0, 0.0])


def account_sparklines(session: Session, *, days: int = 14, today: date | None = None) -> dict[int, dict]:
    today = today or date.today()
    start = datetime.combine(today - timedelta(days=days - 1), time.min)
    end = datetime.combine(today, time.max)
    buckets = _iter_buckets(start, end, "day")
    bookie_delta: dict[int, dict[datetime, Decimal]] = {}
    exchange_delta: dict[int, dict[datetime, Decimal]] = {}
    for bet in _load_bets(session):
        if bet.status == BetStatus.PENDING:
            continue
        when = _bet_when(bet)
        if when < start or when > end:
            continue
        stamp = _bucket_start(when, "day")
        amounts = settlement_amounts(bet)
        bookie_delta.setdefault(bet.bookie_id, {})
        bookie_delta[bet.bookie_id][stamp] = bookie_delta[bet.bookie_id].get(stamp, ZERO) + amounts["bookie"]
        exchange_delta.setdefault(bet.exchange_id, {})
        exchange_delta[bet.exchange_id][stamp] = exchange_delta[bet.exchange_id].get(stamp, ZERO) + amounts["exchange"]

    def pack(deltas: dict[datetime, Decimal]) -> dict:
        running = ZERO
        values = []
        for stamp in buckets:
            running += deltas.get(stamp, ZERO)
            values.append(_num(running))
        return spark_payload(values)

    out: dict[int, dict] = {}
    for account_id, deltas in bookie_delta.items():
        out[account_id] = pack(deltas)
    for account_id, deltas in exchange_delta.items():
        out[account_id] = pack(deltas)
    return out


def apply_sparklines(snapshots: list[dict], sparks: dict[int, dict]) -> None:
    blank = empty_spark()
    for snap in snapshots:
        account = snap["account"]
        snap["spark"] = sparks.get(account.id, blank)


def _labelize(raw: str | None) -> str:
    key = (raw or "").strip()
    return _OFFER_LABELS.get(key) or _BET_LABELS.get(key) or key.replace("_", " ").title() or "—"


def _bet_when(bet: Bet) -> datetime:
    if bet.settled_at:
        return bet.settled_at
    if bet.placed_at:
        return bet.placed_at
    return datetime.combine(bet.date_placed, time.min)


def _placed_when(bet: Bet) -> datetime:
    if bet.placed_at:
        return bet.placed_at
    return datetime.combine(bet.date_placed, time.min)


def range_window(range_key: str, today: date | None = None) -> tuple[datetime, datetime, str]:
    today = today or date.today()
    key = (range_key or "1W").upper()
    if key == "1D":
        start = datetime.combine(today, time.min)
        end = datetime.combine(today, time.max)
        return start, end, "hour"
    if key == "1M":
        start = datetime.combine(today - timedelta(days=29), time.min)
        end = datetime.combine(today, time.max)
        return start, end, "day"
    if key == "1Y":
        year, month = today.year - 1, today.month
        if month == 12:
            year, month = today.year, 1
        else:
            month += 1
        start = datetime(year, month, 1)
        end = datetime.combine(today, time.max)
        return start, end, "month"
    start = datetime.combine(today - timedelta(days=6), time.min)
    end = datetime.combine(today, time.max)
    return start, end, "day"


def custom_window(start_date: date, end_date: date) -> tuple[datetime, datetime, str]:
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    start = datetime.combine(start_date, time.min)
    end = datetime.combine(end_date, time.max)
    span = (end_date - start_date).days
    if span <= 1:
        grain = "hour"
    elif span <= 90:
        grain = "day"
    else:
        grain = "month"
    return start, end, grain


def _hour_floor(when: datetime) -> datetime:
    return when.replace(minute=0, second=0, microsecond=0)


def _month_floor(when: datetime) -> datetime:
    return datetime(when.year, when.month, 1)


def _bucket_start(when: datetime, grain: str) -> datetime:
    if grain == "hour":
        return _hour_floor(when)
    if grain == "month":
        return _month_floor(when)
    return datetime.combine(when.date(), time.min)


def _next_bucket(when: datetime, grain: str) -> datetime:
    if grain == "hour":
        return when + timedelta(hours=1)
    if grain == "month":
        last = monthrange(when.year, when.month)[1]
        return datetime.combine(date(when.year, when.month, last) + timedelta(days=1), time.min)
    return when + timedelta(days=1)


def _axis_label(when: datetime, grain: str, range_key: str | None) -> str:
    if grain == "hour":
        return when.strftime("%H:%M")
    if grain == "month":
        return when.strftime("%b")
    if range_key == "1W":
        return when.strftime("%a")
    return when.strftime("%d/%m")


def _iter_buckets(start: datetime, end: datetime, grain: str) -> list[datetime]:
    cursor = _bucket_start(start, grain)
    last = _bucket_start(end, grain)
    out: list[datetime] = []
    while cursor <= last:
        out.append(cursor)
        cursor = _next_bucket(cursor, grain)
    return out or [last]


def _matches(
    bet: Bet,
    *,
    bookie_id: int | None = None,
    exchange_id: int | None = None,
    bet_type: str | None = None,
    offer_id: int | None = None,
) -> bool:
    if bookie_id and bet.bookie_id != bookie_id:
        return False
    if exchange_id and bet.exchange_id != exchange_id:
        return False
    if bet_type and bet.bet_type != bet_type:
        return False
    if offer_id and bet.offer_id != offer_id:
        return False
    return True


def _load_bets(session: Session) -> list[Bet]:
    return list(
        session.scalars(
            select(Bet).options(
                selectinload(Bet.bookie),
                selectinload(Bet.exchange),
                selectinload(Bet.offer),
            )
        )
    )


def _profit_for(bet: Bet, side: str) -> Decimal:
    if side in {"bookie", "exchange"}:
        return money(settlement_amounts(bet)[side])
    return money(bet.actual_profit or ZERO)


def profit_series(
    session: Session,
    *,
    range_key: str = "1W",
    start: datetime | None = None,
    end: datetime | None = None,
    grain: str | None = None,
    bookie_id: int | None = None,
    exchange_id: int | None = None,
    bet_type: str | None = None,
    offer_id: int | None = None,
    side: str = "net",
    today: date | None = None,
) -> dict:
    if start is None or end is None or grain is None:
        start, end, grain = range_window(range_key, today)
    bets = [bet for bet in _load_bets(session) if _matches(
        bet, bookie_id=bookie_id, exchange_id=exchange_id, bet_type=bet_type, offer_id=offer_id
    )]
    settled = [bet for bet in bets if bet.status != BetStatus.PENDING]
    pending = [bet for bet in bets if bet.status == BetStatus.PENDING]
    buckets = _iter_buckets(start, end, grain)
    delta = {stamp: ZERO for stamp in buckets}
    period = ZERO
    for bet in settled:
        when = _bet_when(bet)
        profit = _profit_for(bet, side)
        if start <= when <= end:
            stamp = _bucket_start(when, grain)
            if stamp in delta:
                delta[stamp] += profit
            period += profit
    running = ZERO
    values = []
    labels = []
    for stamp in buckets:
        running += delta[stamp]
        values.append(_num(running))
        labels.append(_axis_label(stamp, grain, range_key))
    return {
        "view": "profit_time",
        "kind": "area",
        "title": _VIEW_TITLES["profit_time"],
        "range": range_key,
        "total": _num(period),
        "pending": _num(_sum(bet.expected_profit for bet in pending)),
        "labels": labels,
        "values": values,
        "empty": not any(value != 0 for value in values),
        "from": format_uk(start.date()),
        "to": format_uk(end.date()),
    }


def _bar_payload(view: str, rows: list[tuple[str, Decimal]], *, extra: dict | None = None) -> dict:
    rows = [(label, money(value)) for label, value in rows if label]
    rows.sort(key=lambda item: abs(item[1]), reverse=True)
    payload = {
        "view": view,
        "kind": "bar",
        "title": _VIEW_TITLES[view],
        "total": _num(_sum(value for _, value in rows)),
        "pending": 0.0,
        "labels": [label for label, _ in rows],
        "values": [_num(value) for _, value in rows],
        "empty": not rows,
    }
    if extra:
        payload.update(extra)
    return payload


def breakdown_series(
    session: Session,
    view: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    bookie_id: int | None = None,
    exchange_id: int | None = None,
    bet_type: str | None = None,
    offer_id: int | None = None,
) -> dict:
    bets = [bet for bet in _load_bets(session) if _matches(
        bet, bookie_id=bookie_id, exchange_id=exchange_id, bet_type=bet_type, offer_id=offer_id
    )]
    if start and end:
        bets = [bet for bet in bets if start <= _bet_when(bet) <= end]
    settled = [bet for bet in bets if bet.status != BetStatus.PENDING]
    grouped: dict[str, Decimal] = {}

    def add(label: str, value: Decimal) -> None:
        grouped[label] = grouped.get(label, ZERO) + money(value)

    if view == "by_bookie":
        for bet in settled:
            add(bet.bookie.name if bet.bookie else "—", bet.actual_profit or ZERO)
    elif view == "by_exchange":
        for bet in settled:
            add(bet.exchange.name if bet.exchange else "—", money(settlement_amounts(bet)["exchange"]))
    elif view == "by_offer_type":
        for bet in settled:
            offer_type = bet.offer.type if bet.offer else "other"
            add(_labelize(offer_type), bet.actual_profit or ZERO)
    elif view == "by_bet_type":
        for bet in settled:
            add(_labelize(bet.bet_type), bet.actual_profit or ZERO)
    elif view == "by_offer":
        for bet in settled:
            name = bet.offer.name if bet.offer else "No offer"
            add(name, bet.actual_profit or ZERO)
    elif view == "outcomes":
        counts: dict[str, int] = {}
        for bet in bets:
            label = _OUTCOME_LABELS.get(bet.status, bet.status)
            counts[label] = counts.get(label, 0) + 1
            add(label, bet.actual_profit or ZERO if bet.status != BetStatus.PENDING else ZERO)
        rows = list(grouped.items())
        payload = _bar_payload(view, rows)
        payload["counts"] = [counts.get(label, 0) for label in payload["labels"]]
        return payload
    else:
        return _bar_payload(view, [])

    return _bar_payload(view, list(grouped.items()))


def activity_series(
    session: Session,
    *,
    start: datetime,
    end: datetime,
    grain: str,
    range_key: str = "",
    bookie_id: int | None = None,
    exchange_id: int | None = None,
    bet_type: str | None = None,
    offer_id: int | None = None,
) -> dict:
    bets = [
        bet
        for bet in _load_bets(session)
        if _matches(bet, bookie_id=bookie_id, exchange_id=exchange_id, bet_type=bet_type, offer_id=offer_id)
        and start <= _placed_when(bet) <= end
    ]
    buckets = _iter_buckets(start, end, grain)
    counts = {stamp: 0 for stamp in buckets}
    for bet in bets:
        stamp = _bucket_start(_placed_when(bet), grain)
        if stamp in counts:
            counts[stamp] += 1
    labels = [_axis_label(stamp, grain, range_key) for stamp in buckets]
    values = [float(counts[stamp]) for stamp in buckets]
    return {
        "view": "activity",
        "kind": "area",
        "title": _VIEW_TITLES["activity"],
        "total": float(len(bets)),
        "pending": 0.0,
        "labels": labels,
        "values": values,
        "empty": not bets,
        "unit": "bets",
    }


def balances_series(session: Session) -> dict:
    accounts = list(session.scalars(select(Account).order_by(Account.name)))
    rows = []
    for account in accounts:
        snap = account_snapshot(session, account)
        if snap["balance"] == ZERO and snap["net_profit"] == ZERO and snap["deposited"] == ZERO:
            continue
        rows.append((account.name, snap["balance"]))
    payload = _bar_payload("balances", rows)
    payload["total"] = _num(_sum(value for _, value in rows))
    return payload


def accounts_profit_bars(session: Session) -> dict:
    accounts = list(session.scalars(select(Account).order_by(Account.name)))
    rows = []
    for account in accounts:
        snap = account_snapshot(session, account)
        if snap["net_profit"] == ZERO and snap["balance"] == ZERO and snap["deposited"] == ZERO:
            continue
        rows.append((account.name, snap["net_profit"]))
    return _bar_payload("by_bookie", rows, extra={"title": "Profit by account", "view": "by_account"})


def cashflow_series(
    session: Session,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    bookie_id: int | None = None,
    account_id: int | None = None,
) -> dict:
    query = select(Transfer).options(selectinload(Transfer.account))
    transfers = list(session.scalars(query))
    deposited = ZERO
    withdrawn = ZERO
    opening = ZERO
    wanted = account_id or bookie_id
    for transfer in transfers:
        when = datetime.combine(transfer.date, time.min)
        if start and when < start:
            continue
        if end and when > end:
            continue
        if wanted and transfer.account_id != wanted:
            continue
        if transfer.kind == TransferKind.DEPOSIT:
            deposited += money(transfer.amount)
        elif transfer.kind == TransferKind.WITHDRAWAL:
            withdrawn += money(transfer.amount)
        elif transfer.kind == TransferKind.OPENING:
            opening += money(transfer.amount)
    rows = [
        ("Deposits", deposited),
        ("Withdrawals", withdrawn),
        ("Opening", opening),
    ]
    return _bar_payload("cashflow", rows)


def visualiser_payload(
    session: Session,
    *,
    view: str = "profit_time",
    range_key: str = "1W",
    start_raw: str | None = None,
    end_raw: str | None = None,
    bookie_id: int | None = None,
    exchange_id: int | None = None,
    bet_type: str | None = None,
    offer_id: int | None = None,
    account_id: int | None = None,
    today: date | None = None,
) -> dict:
    chosen = view if view in VIEWS else "profit_time"
    today = today or date.today()
    side = "net"
    cash_id = bookie_id
    if account_id:
        account = session.get(Account, account_id)
        if account is not None:
            cash_id = account.id
            if account.is_bookie:
                bookie_id = bookie_id or account.id
            else:
                exchange_id = exchange_id or account.id
                side = "exchange"
    if (start_raw or "").strip() or (end_raw or "").strip():
        start_date = parse_uk(start_raw, today)
        end_date = parse_uk(end_raw, today)
        start, end, grain = custom_window(start_date, end_date)
        range_key = "custom"
    else:
        start, end, grain = range_window(range_key, today)

    filters = {
        "bookie_id": bookie_id,
        "exchange_id": exchange_id,
        "bet_type": bet_type,
        "offer_id": offer_id,
    }
    if chosen == "profit_time":
        payload = profit_series(
            session,
            range_key=range_key,
            start=start,
            end=end,
            grain=grain,
            today=today,
            side=side,
            **filters,
        )
    elif chosen == "activity":
        payload = activity_series(
            session, start=start, end=end, grain=grain, range_key=range_key, **filters
        )
    elif chosen == "balances":
        payload = balances_series(session)
    elif chosen == "cashflow":
        payload = cashflow_series(session, start=start, end=end, bookie_id=cash_id)
    else:
        payload = breakdown_series(session, chosen, start=start, end=end, **filters)
    payload["from"] = format_uk(start.date())
    payload["to"] = format_uk(end.date())
    payload["range"] = range_key
    return payload
