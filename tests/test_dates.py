from datetime import date

import pytest

from app.dates import format_uk, parse_uk


def test_uk_date_round_trip():
    assert format_uk(date(2026, 8, 28)) == "28/08/2026"
    assert parse_uk("28/08/2026") == date(2026, 8, 28)


def test_parse_uk_accepts_iso_too():
    assert parse_uk("2026-08-28") == date(2026, 8, 28)


def test_parse_uk_rejects_nonsense():
    with pytest.raises(ValueError, match="DD/MM/YYYY"):
        parse_uk("13/40/2026")
