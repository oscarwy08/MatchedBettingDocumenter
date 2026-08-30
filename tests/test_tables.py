from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from app.models import Account, Bet, BetStatus, BetType, Offer, OfferType


def _seed_bet(session):
    bookie = session.scalars(select(Account).where(Account.name == "Betfred")).one()
    exchange = session.scalars(select(Account).where(Account.name == "Smarkets")).one()
    offer = Offer(name="Betfred welcome", type=OfferType.WELCOME, bookie_id=bookie.id)
    session.add(offer)
    session.flush()
    bet = Bet(
        event="Kickoff",
        bet_type=BetType.NORMAL,
        bookie_id=bookie.id,
        exchange_id=exchange.id,
        offer_id=offer.id,
        back_stake=Decimal("10"),
        back_odds=Decimal("2"),
        lay_stake=Decimal("10"),
        lay_odds=Decimal("2.1"),
        commission_percent=Decimal("2"),
        cashback=Decimal("0"),
        liability=Decimal("11"),
        expected_profit=Decimal("0"),
        expected_bookie_back=Decimal("10"),
        expected_exchange_back=Decimal("-9"),
        expected_bookie_lay=Decimal("-10"),
        expected_exchange_lay=Decimal("9.80"),
        status=BetStatus.PENDING,
        placed_at=datetime(2026, 8, 29, 14, 10),
        starts_at=datetime(2026, 8, 30, 19, 45),
    )
    session.add(bet)
    session.commit()
    return bookie.id, offer.id, bet.id


def _assert_bet_times(page):
    assert page.status_code == 200
    assert b"<th>Placed</th>" in page.data
    assert b"<th>Starts</th>" in page.data
    assert b"29/08/2026 14:10" in page.data
    assert b"30/08/2026 19:45" in page.data


def test_every_bets_table_has_placed_and_starts(tmp_path, monkeypatch):
    root = tmp_path / "root"
    monkeypatch.setenv("MBD_ROOT", str(root))
    import app

    monkeypatch.setattr(app, "ROOT_DIR", root)
    monkeypatch.setattr(app, "DATA_DIR", root / "data")
    monkeypatch.setattr(app, "DB_PATH", root / "data" / "app.db")
    client = app.create_app().test_client()
    import app.db as db

    session = db.SessionLocal()
    bookie_id, offer_id, bet_id = _seed_bet(session)
    session.close()

    _assert_bet_times(client.get("/"))
    _assert_bet_times(client.get("/bets"))
    _assert_bet_times(client.get(f"/accounts/{bookie_id}"))
    _assert_bet_times(client.get(f"/offers/{offer_id}"))
    detail = client.get(f"/bets/{bet_id}")
    assert detail.status_code == 200
    assert b">Placed<" in detail.data
    assert b">Starts<" in detail.data
    assert b"29/08/2026 14:10" in detail.data
    assert b"30/08/2026 19:45" in detail.data
    assert b"tables.js" in client.get("/bets").data
    assert b"tables.js" in client.get("/offers").data
