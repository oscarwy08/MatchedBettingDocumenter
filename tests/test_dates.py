from datetime import date, datetime

import pytest

from app.dates import combine_date, format_uk, format_uk_time, parse_uk


def test_uk_date_round_trip():
    assert format_uk(date(2026, 8, 28)) == "28/08/2026"
    assert parse_uk("28/08/2026") == date(2026, 8, 28)


def test_parse_uk_accepts_iso_too():
    assert parse_uk("2026-08-28") == date(2026, 8, 28)


def test_parse_uk_rejects_nonsense():
    with pytest.raises(ValueError, match="DD/MM/YYYY"):
        parse_uk("13/40/2026")


def test_uk_time_hides_midnight_and_shows_clock():
    assert format_uk_time(None) == "–"
    assert format_uk_time(date(2026, 8, 28)) == "28/08/2026"
    assert format_uk_time(datetime(2026, 8, 28, 0, 0)) == "28/08/2026"
    assert format_uk_time(datetime(2026, 8, 28, 14, 5)) == "28/08/2026 14:05"


def test_combine_date_keeps_existing_clock():
    kept = combine_date(datetime(2026, 1, 2, 15, 40), date(2026, 8, 28))
    assert kept == datetime(2026, 8, 28, 15, 40)
