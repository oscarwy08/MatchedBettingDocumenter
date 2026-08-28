from decimal import Decimal

from sqlalchemy import select

from app.db import init_db
from app.models import Account, Bet, BetStatus, BetType
from app.seed import seed_accounts
from app.snapshot import apply_snapshot, dump_snapshot
from app.sync import make_link_code, parse_link_code


def test_parse_link_code():
    pin, host = parse_link_code(" 482193@192.168.1.10:5050 ")
    assert pin == "482193"
    assert host == "192.168.1.10:5050"
    pin, host = parse_link_code("482193@10.0.0.5")
    assert host == "10.0.0.5:5050"


def test_snapshot_round_trip(tmp_path):
    Session = init_db(tmp_path / "a.db")
    session = Session()
    seed_accounts(session)
    sky = session.scalars(select(Account).where(Account.name == "Sky Bet")).one()
    smarkets = session.scalars(select(Account).where(Account.name == "Smarkets")).one()
    session.add(
        Bet(
            event="Keep me",
            bet_type=BetType.QUALIFYING,
            bookie_id=sky.id,
            exchange_id=smarkets.id,
            back_stake=Decimal("10.00"),
            back_odds=Decimal("2.00"),
            lay_stake=Decimal("9.62"),
            lay_odds=Decimal("2.10"),
            commission_percent=Decimal("2"),
            cashback=Decimal("0"),
            liability=Decimal("10.58"),
            expected_profit=Decimal("-0.58"),
            expected_bookie_back=Decimal("10"),
            expected_exchange_back=Decimal("-10.58"),
            expected_bookie_lay=Decimal("-10"),
            expected_exchange_lay=Decimal("9.43"),
            status=BetStatus.PENDING,
        )
    )
    session.commit()
    payload = dump_snapshot(session)
    session.close()

    Session2 = init_db(tmp_path / "b.db")
    other = Session2()
    seed_accounts(other)
    apply_snapshot(other, payload)
    other.commit()
    copied = other.scalars(select(Bet)).one()
    assert copied.event == "Keep me"
    assert copied.back_stake == Decimal("10.00")
    other.add(Account(name="Brand New Bookie", type="bookie", commission_percent=Decimal("0")))
    other.commit()
    fresh = other.scalars(select(Account).where(Account.name == "Brand New Bookie")).one()
    assert fresh.id > copied.bookie_id
    other.close()
    assert "482193" in make_link_code("482193", 5050)
