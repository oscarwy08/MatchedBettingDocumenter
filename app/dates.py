from __future__ import annotations

from datetime import date, datetime, time


def local_now() -> datetime:
    return datetime.now()


def format_uk(value: date | datetime | None) -> str:
    if value is None:
        return "–"
    if isinstance(value, datetime):
        value = value.date()
    return value.strftime("%d/%m/%Y")


def format_uk_time(value: date | datetime | None) -> str:
    if value is None:
        return "–"
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    clock = value.time()
    if clock == time.min:
        return value.strftime("%d/%m/%Y")
    return value.strftime("%d/%m/%Y %H:%M")


def combine_date(existing: datetime | None, new_date: date) -> datetime:
    if existing is not None:
        return datetime.combine(new_date, existing.time())
    return datetime.combine(new_date, local_now().time())


def parse_uk(raw: str | None, fallback: date | None = None) -> date:
    text = (raw or "").strip()
    if not text:
        return fallback if fallback is not None else date.today()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError("Use dates as DD/MM/YYYY.")
