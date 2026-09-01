from datetime import datetime
from decimal import Decimal
from pathlib import Path

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
    table = client.get("/bets")
    assert b">Table</a>" in table.data
    assert b">By event</a>" in table.data
    events = client.get("/bets?view=events")
    assert events.status_code == 200
    assert b"event-card" in events.data
    assert b"Kickoff" in events.data
    assert b"Worst" in events.data
    assert b"Best" in events.data
    assert b"<th>Placed</th>" not in events.data
    assert "⚽".encode() not in events.data
    assert "🏇".encode() not in events.data


def test_bet_details_use_date_pickers(tmp_path, monkeypatch):
    root = tmp_path / "root"
    monkeypatch.setenv("MBD_ROOT", str(root))
    import app

    monkeypatch.setattr(app, "ROOT_DIR", root)
    monkeypatch.setattr(app, "DATA_DIR", root / "data")
    monkeypatch.setattr(app, "DB_PATH", root / "data" / "app.db")
    client = app.create_app().test_client()
    import app.db as db

    session = db.SessionLocal()
    bookie_id, _offer_id, bet_id = _seed_bet(session)
    exchange_id = session.scalars(select(Account).where(Account.name == "Smarkets")).one().id
    session.close()

    calc = client.get("/calculator")
    assert calc.status_code == 200
    assert b'name="date_placed" type="date"' in calc.data
    assert b'name="starts_at" type="datetime-local"' in calc.data
    assert b'name="ends_at" type="datetime-local"' in calc.data
    assert b"fixtures.js" in calc.data
    bet_panel, _, _rest = calc.data.partition(b"Log this bet")
    assert b'id="selections-field"' in bet_panel
    assert b'name="market"' in bet_panel
    assert b">Selections</span>" in bet_panel
    js = (Path(__file__).resolve().parents[1] / "app" / "static" / "calculator.js").read_text(encoding="utf-8")
    assert "WITH_SELECTIONS" not in js
    assert 'selections.classList.toggle("is-hidden"' not in js

    edit = client.get(f"/bets/{bet_id}/edit")
    assert edit.status_code == 200
    assert b'type="date"' in edit.data
    assert b'type="datetime-local"' in edit.data
    assert b"2026-08-30T19:45" in edit.data

    logged = client.post(
        "/calculator/log",
        data={
            "bet_type": "normal",
            "back_stake": "10",
            "back_odds": "2",
            "lay_odds": "2.1",
            "commission_percent": "2",
            "cashback": "0",
            "bookie_id": str(bookie_id),
            "exchange_id": str(exchange_id),
            "date_placed": "2026-09-01",
            "starts_at": "2026-09-02T15:30",
            "event": "Picker cup",
            "market": "Match odds / Liverpool",
        },
        follow_redirects=True,
    )
    assert logged.status_code == 200
    session = db.SessionLocal()
    saved = session.scalars(select(Bet).where(Bet.event == "Picker cup")).one()
    assert saved.date_placed.isoformat() == "2026-09-01"
    assert saved.starts_at == datetime(2026, 9, 2, 15, 30)
    assert saved.market == "Match odds / Liverpool"
    session.close()
