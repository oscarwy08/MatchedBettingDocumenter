"""Group matched bets by event for the By event bets view."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

from app.dates import format_uk_time, parse_uk_datetime
from app.models import BetStatus


def group_bets(bets, *, now: datetime | None = None) -> list[dict]:
    """Return event cards ordered by start time: upcoming first, then past, then undated."""
    now = now or datetime.now()
    buckets: dict[str, dict] = {}
    order: list[str] = []
    for bet in bets:
        key, meta = _group_key(bet)
        bucket = buckets.get(key)
        if bucket is None:
            bucket = {
                "key": key,
                "title": meta["title"],
                "sport": meta["sport"],
                "starts_at": meta["starts_at"],
                "worst": Decimal("0.00"),
                "best": Decimal("0.00"),
                "pending": False,
                "rows": [],
            }
            buckets[key] = bucket
            order.append(key)
        elif meta["starts_at"] and (
            bucket["starts_at"] is None or meta["starts_at"] < bucket["starts_at"]
        ):
            bucket["starts_at"] = meta["starts_at"]
        worst, best = _outcomes(bet)
        bucket["worst"] += worst
        bucket["best"] += best
        if _pending(bet):
            bucket["pending"] = True
        bucket["rows"].append(_row(bet))

    groups = []
    for key in order:
        bucket = buckets[key]
        start = bucket["starts_at"]
        groups.append(
            {
                **bucket,
                "count": len(bucket["rows"]),
                "starts_label": format_uk_time(start) if start else "",
            }
        )
    return sorted(groups, key=lambda group: _sort_key(group, now))


def _sort_key(group: dict, now: datetime) -> tuple:
    start = group.get("starts_at")
    if start is None:
        return (2, datetime.max)
    if start >= now:
        return (0, start)
    return (1, datetime.max - start)


def _group_key(bet) -> tuple[str, dict]:
    source = str(_attr(bet, "fixture_source") or "").strip()
    ident = str(_attr(bet, "fixture_id") or "").strip()
    event = str(_attr(bet, "event") or "").strip() or "Untitled"
    starts = _starts(bet)
    if source and ident:
        key = f"fix:{source}:{ident}"
    else:
        day = starts.date().isoformat() if starts else ""
        key = f"ev:{event.casefold()}|{day}"
    return key, {"title": event, "sport": source, "starts_at": starts}


def _row(bet) -> dict:
    stake = _money(_attr(bet, "back_stake"))
    odds = _money(_attr(bet, "back_odds"))
    ret = stake * odds if stake and odds else None
    pending = _pending(bet)
    return {
        "id": _attr(bet, "id"),
        "event": str(_attr(bet, "event") or "Untitled"),
        "market": str(_attr(bet, "market") or ""),
        "bookie": _rel_name(bet, "bookie"),
        "bookie_id": _attr(bet, "bookie_id"),
        "exchange": _rel_name(bet, "exchange"),
        "exchange_id": _attr(bet, "exchange_id"),
        "offer": _rel_name(bet, "offer"),
        "offer_id": _attr(bet, "offer_id"),
        "back_odds": _attr(bet, "back_odds"),
        "lay_odds": _attr(bet, "lay_odds"),
        "back_stake": _attr(bet, "back_stake"),
        "liability": _attr(bet, "liability"),
        "ret": ret,
        "expected_profit": _attr(bet, "expected_profit"),
        "actual_profit": _attr(bet, "actual_profit"),
        "pending": pending,
        "status": _attr(bet, "status") or ("pending" if pending else ""),
    }


def _outcomes(bet) -> tuple[Decimal, Decimal]:
    if _pending(bet):
        back = _money(_attr(bet, "expected_bookie_back")) + _money(_attr(bet, "expected_exchange_back"))
        lay = _money(_attr(bet, "expected_bookie_lay")) + _money(_attr(bet, "expected_exchange_lay"))
        if back == 0 and lay == 0:
            expected = _money(_attr(bet, "expected_profit"))
            return expected, expected
        return min(back, lay), max(back, lay)
    actual = _attr(bet, "actual_profit")
    if actual in (None, ""):
        actual = _attr(bet, "profit")
    amount = _money(actual)
    return amount, amount


def _pending(bet) -> bool:
    if isinstance(bet, dict) and bet.get("pending"):
        return True
    return str(_attr(bet, "status") or "") == BetStatus.PENDING


def _starts(bet) -> datetime | None:
    raw = _attr(bet, "starts_at")
    if raw in (None, "") and isinstance(bet, dict):
        raw = bet.get("starts")
    return _as_datetime(raw)


def _as_datetime(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    text = str(value).strip()
    if not text:
        return None
    parsed = parse_uk_datetime(text)
    if parsed is not None:
        return parsed
    try:
        return datetime.fromisoformat(text.replace("Z", ""))
    except ValueError:
        return None


def _attr(bet, name: str):
    if isinstance(bet, dict):
        return bet.get(name)
    return getattr(bet, name, None)


def _rel_name(bet, rel: str) -> str:
    if isinstance(bet, dict):
        return str(bet.get(rel) or "")
    obj = getattr(bet, rel, None)
    if obj is None:
        return ""
    return str(getattr(obj, "name", "") or "")


def _money(value) -> Decimal:
    if value is None or value == "":
        return Decimal("0.00")
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return Decimal("0.00")
