from datetime import datetime
from decimal import Decimal

from app.event_groups import group_bets


def test_groups_same_fixture_and_orders_by_start():
    later = {
        "id": 2,
        "event": "16:06 Brighton",
        "market": "To win / Sangara",
        "fixture_source": "racing",
        "fixture_id": "br-1606",
        "starts_at": "2026-09-01T16:06",
        "status": "pending",
        "pending": True,
        "back_stake": "10.00",
        "back_odds": "10.0",
        "lay_odds": "11.0",
        "liability": "100.00",
        "expected_bookie_back": "90.00",
        "expected_exchange_back": "-100.00",
        "expected_bookie_lay": "-10.00",
        "expected_exchange_lay": "8.80",
        "bookie": "Sky Bet",
        "bookie_id": 1,
    }
    earlier = {
        **later,
        "id": 1,
        "event": "Liverpool vs Chelsea",
        "market": "Match odds / Liverpool",
        "fixture_source": "football",
        "fixture_id": "fd-99",
        "starts_at": "2026-09-01T15:00",
        "expected_bookie_back": "10.00",
        "expected_exchange_back": "-10.58",
        "expected_bookie_lay": "-10.00",
        "expected_exchange_lay": "9.43",
        "liability": "10.58",
        "back_odds": "2.00",
    }
    same_race = {
        **later,
        "id": 3,
        "expected_bookie_back": "5.00",
        "expected_exchange_back": "-6.00",
        "expected_bookie_lay": "-5.00",
        "expected_exchange_lay": "4.80",
        "liability": "6.00",
        "back_stake": "5.00",
        "back_odds": "2.00",
    }
    groups = group_bets([later, earlier, same_race], now=datetime(2026, 8, 31, 12, 0))
    assert [group["title"] for group in groups] == ["Liverpool vs Chelsea", "16:06 Brighton"]
    assert groups[0]["sport"] == "football"
    assert groups[0]["count"] == 1
    assert groups[1]["count"] == 2
    assert groups[1]["worst"] == Decimal("-11.00")
    assert groups[1]["best"] == Decimal("-1.40")
    assert groups[1]["pending"] is True


def test_settled_uses_actual_and_undated_goes_last():
    settled = {
        "id": 4,
        "event": "Old cup",
        "starts_at": "2026-08-01T15:00",
        "status": "back_won",
        "pending": False,
        "actual_profit": "1.50",
        "bookie": "Sky Bet",
    }
    undated = {
        "id": 5,
        "event": "Mystery",
        "status": "pending",
        "pending": True,
        "expected_profit": "-0.40",
        "bookie": "Betfred",
    }
    groups = group_bets([undated, settled], now=datetime(2026, 8, 31, 12, 0))
    assert groups[0]["title"] == "Old cup"
    assert groups[0]["worst"] == Decimal("1.50")
    assert groups[0]["best"] == Decimal("1.50")
    assert groups[1]["title"] == "Mystery"
    assert groups[1]["starts_at"] is None
