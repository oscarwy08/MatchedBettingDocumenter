from __future__ import annotations

from datetime import date, datetime


def format_uk(value: date | datetime | None) -> str:
    if value is None:
        return "–"
    if isinstance(value, datetime):
        value = value.date()
    return value.strftime("%d/%m/%Y")


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
