from decimal import Decimal

from sqlalchemy import select

from app.models import Account, Bet, BetStatus, BetType


def test_bets_table_has_starts_and_sort_script(tmp_path, monkeypatch):
    root = tmp_path / "root"
    monkeypatch.setenv("MBD_ROOT", str(root))
    import app

    monkeypatch.setattr(app, "ROOT_DIR", root)
    monkeypatch.setattr(app, "DATA_DIR", root / "data")
    monkeypatch.setattr(app, "DB_PATH", root / "data" / "app.db")
    client = app.create_app().test_client()
    import app.db as db

    session = db.SessionLocal()
    bookie = session.scalars(select(Account).where(Account.name == "Betfred")).one()
    exchange = session.scalars(select(Account).where(Account.name == "Smarkets")).one()
    session.add(
        Bet(
            event="Kickoff",
            bet_type=BetType.NORMAL,
            bookie_id=bookie.id,
            exchange_id=exchange.id,
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
        )
    )
    session.commit()
    session.close()
    page = client.get("/bets")
    assert page.status_code == 200
    assert b"<th>Starts</th>" in page.data
    assert b"<th>Placed</th>" not in page.data
    assert b"tables.js" in page.data
    assert b"Kickoff" in page.data
    offers = client.get("/offers")
    assert b"tables.js" in offers.data
