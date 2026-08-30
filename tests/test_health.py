from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.db import init_db
from app.health import mug_health, today_board
from app.models import Account, AccountTask, Bet, BetStatus, BetType, Offer, OfferType
from app.seed import seed_accounts
from app.snapshot import apply_snapshot, dump_snapshot


def _session(tmp_path: Path):
    Session = init_db(tmp_path / "health.db")
    session = Session()
    seed_accounts(session)
    return session


def _bet(session, bookie, exchange, *, bet_type, when, offer=None):
    placed = when if isinstance(when, date) else when.date()
    bet = Bet(
        offer_id=offer.id if offer else None,
        date_placed=placed,
        event="Health",
        bet_type=bet_type,
        bookie_id=bookie.id,
        exchange_id=exchange.id,
        back_stake=Decimal("10.00"),
        back_odds=Decimal("2.00"),
        lay_stake=Decimal("10.00"),
        lay_odds=Decimal("2.10"),
        commission_percent=Decimal("2"),
        cashback=Decimal("0"),
        liability=Decimal("11.00"),
        expected_profit=Decimal("1.00"),
        expected_bookie_back=Decimal("10.00"),
        expected_exchange_back=Decimal("-9.00"),
        expected_bookie_lay=Decimal("-10.00"),
        expected_exchange_lay=Decimal("9.80"),
        status=BetStatus.BACK_WON,
        actual_profit=Decimal("1.00"),
        placed_at=datetime.combine(placed, datetime.min.time()),
    )
    session.add(bet)
    session.flush()
    return bet


def test_mug_then_two_qualifiers_is_red_at_threshold_two(tmp_path: Path):
    session = _session(tmp_path)
    bookie = session.scalars(select(Account).where(Account.name == "Betfred")).one()
    exchange = session.scalars(select(Account).where(Account.name == "Smarkets")).one()
    _bet(session, bookie, exchange, bet_type=BetType.MUG, when=date(2026, 8, 1))
    _bet(session, bookie, exchange, bet_type=BetType.QUALIFYING, when=date(2026, 8, 10))
    _bet(session, bookie, exchange, bet_type=BetType.QUALIFYING, when=date(2026, 8, 20))
    bets = list(session.scalars(select(Bet)))
    amber = mug_health(bookie, bets[:2], threshold=2, today=date(2026, 8, 30))
    assert amber["level"] == "amber"
    assert amber["promo_since"] == 1
    health = mug_health(bookie, bets, threshold=2, today=date(2026, 8, 30))
    assert health["level"] == "red"
    assert health["promo_since"] == 2
    assert health["percent"] == 0
    assert amber["percent"] == 50
    assert health["mugs"] == 1
    assert "qualifier" in health["label"]
    session.close()


def test_no_promo_bets_is_green(tmp_path: Path):
    session = _session(tmp_path)
    bookie = session.scalars(select(Account).where(Account.name == "Coral")).one()
    health = mug_health(bookie, [], threshold=4, today=date(2026, 8, 30))
    assert health["level"] == "green"
    assert health["label"] == "Healthy"
    assert health["percent"] == 100
    session.close()


def test_today_list_prefers_reload_due_and_oldest_unchecked(tmp_path: Path):
    session = _session(tmp_path)
    today = date(2026, 8, 30)
    sky = session.scalars(select(Account).where(Account.name == "Sky Bet")).one()
    coral = session.scalars(select(Account).where(Account.name == "Coral")).one()
    betfred = session.scalars(select(Account).where(Account.name == "Betfred")).one()
    for account in session.scalars(select(Account).where(Account.type == "bookie")):
        account.last_checked_on = today - timedelta(days=1)
    sky.last_checked_on = today - timedelta(days=2)
    coral.last_checked_on = None
    betfred.last_checked_on = today - timedelta(days=20)
    exchange = session.scalars(select(Account).where(Account.name == "Smarkets")).one()
    _bet(session, coral, exchange, bet_type=BetType.QUALIFYING, when=today - timedelta(days=3))
    _bet(session, betfred, exchange, bet_type=BetType.QUALIFYING, when=today - timedelta(days=4))
    session.add(
        Offer(
            name="Sky Saturday reload",
            type=OfferType.RELOAD,
            bookie_id=sky.id,
            reload_frequency="weekly",
            next_reload_on=today,
        )
    )
    session.flush()
    board = today_board(
        session,
        today=today,
        settings={"daily_check_target": 3, "check_every_days": 7, "priority_check_days": 3, "mug_after_offers": 4},
    )
    names = [row["account"].name for row in board["routine"] if not row["checked_today"]]
    assert names[0] == "Sky Bet"
    assert names[1] == "Coral"
    assert "Betfred" in names
    assert "10bet" not in names
    unused = today_board(
        session,
        today=today,
        settings={"daily_check_target": 20, "check_every_days": 7, "priority_check_days": 3, "mug_after_offers": 4},
    )
    unused_names = [row["account"].name for row in unused["routine"]]
    assert "10bet" not in unused_names
    assert "Coral" in unused_names
    assert board["specials"][0]["kind"] == "reload"
    assert board["specials"][0]["name"] == "Sky Saturday reload"
    session.close()


def test_yesterday_check_is_not_checked_today(tmp_path: Path):
    session = _session(tmp_path)
    today = date(2026, 8, 30)
    bookie = session.scalars(select(Account).where(Account.name == "Betfred")).one()
    bookie.last_checked_on = today - timedelta(days=1)
    session.flush()
    health = mug_health(bookie, [], threshold=4, today=today)
    assert health["checked_today"] is False
    bookie.last_checked_on = today
    session.flush()
    health = mug_health(bookie, [], threshold=4, today=today)
    assert health["checked_today"] is True
    session.close()


def test_snapshot_round_trip_health_fields_and_task(tmp_path: Path):
    session = _session(tmp_path)
    sky = session.scalars(select(Account).where(Account.name == "Sky Bet")).one()
    sky.last_checked_on = date(2026, 8, 29)
    sky.priority = True
    sky.restriction = "stake_limited"
    sky.notes = "Leave £20 in"
    sky.check_weekday = 5
    session.add(
        AccountTask(
            account_id=sky.id,
            due_on=date(2026, 8, 30),
            note="Check Saturday booster",
        )
    )
    session.commit()
    payload = dump_snapshot(session)
    session.close()
    other = init_db(tmp_path / "copy.db")()
    apply_snapshot(other, payload)
    other.commit()
    copied = other.scalars(select(Account).where(Account.name == "Sky Bet")).one()
    assert copied.last_checked_on == date(2026, 8, 29)
    assert copied.priority is True
    assert copied.restriction == "stake_limited"
    assert copied.notes == "Leave £20 in"
    assert copied.check_weekday == 5
    task = other.scalars(select(AccountTask)).one()
    assert task.note == "Check Saturday booster"
    assert task.due_on == date(2026, 8, 30)
    assert task.done is False
    other.close()


def test_today_page_and_tick(tmp_path, monkeypatch):
    root = tmp_path / "root"
    monkeypatch.setenv("MBD_ROOT", str(root))
    import app

    monkeypatch.setattr(app, "ROOT_DIR", root)
    monkeypatch.setattr(app, "DATA_DIR", root / "data")
    monkeypatch.setattr(app, "DB_PATH", root / "data" / "app.db")
    client = app.create_app().test_client()
    page = client.get("/today")
    assert page.status_code == 200
    assert b"Routine checks" in page.data
    assert b"Due today" in page.data
    assert b'class="week-day' in page.data
    yesterday = (date.today() - timedelta(days=1)).strftime("%d/%m/%Y")
    other = client.get(f"/today?on={yesterday}")
    assert other.status_code == 200
    assert yesterday.encode() in other.data
    assert b"Back to today" in other.data
    accounts = client.get("/accounts")
    assert accounts.status_code == 200
    assert b"health-ring" in accounts.data
    calc = client.get("/calculator?bet_type=mug&bookie_id=1")
    assert calc.status_code == 200
    assert b"Mug bet" in calc.data
    import app.db as db

    session = db.SessionLocal()
    bookie = session.scalars(select(Account).where(Account.name == "Betfred")).one()
    bookie.last_checked_on = date.today() - timedelta(days=1)
    bookie_id = bookie.id
    session.commit()
    session.close()
    ticked = client.post(f"/accounts/{bookie_id}/checked", data={"next": "today"}, follow_redirects=True)
    assert ticked.status_code == 200
    session = db.SessionLocal()
    bookie = session.get(Account, bookie_id)
    assert bookie.last_checked_on == date.today()
    session.close()
