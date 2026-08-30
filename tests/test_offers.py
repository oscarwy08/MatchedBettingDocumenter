from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.db import init_db
from app.models import Account, Bet, BetStatus, BetType, Offer, OfferType
from app.seed import seed_accounts
from app.services import advance_reload, next_reload_after, offer_snapshot
from app.snapshot import apply_snapshot, dump_snapshot


def _session(tmp_path):
    Session = init_db(tmp_path / "app.db")
    session = Session()
    seed_accounts(session)
    return session


def test_weekly_reload_advances():
    assert next_reload_after("weekly", date(2026, 8, 30)) == date(2026, 9, 6)
    assert next_reload_after("monthly", date(2026, 1, 31)) == date(2026, 2, 28)


def test_reload_offer_stays_in_progress_and_can_be_claimed(tmp_path):
    session = _session(tmp_path)
    sky = session.scalars(select(Account).where(Account.name == "Sky Bet")).one()
    offer = Offer(
        name="Sky weekly",
        type=OfferType.RELOAD,
        bookie_id=sky.id,
        free_funds=Decimal("5"),
        reload_frequency="weekly",
        reload_stake=Decimal("20"),
        reload_reward=Decimal("5"),
        next_reload_on=date(2026, 8, 30),
    )
    session.add(offer)
    session.flush()
    snap = offer_snapshot(offer)
    assert snap["reload_due"] is True
    assert snap["status"] == "Reload due"
    assert snap["reload_stake"] == Decimal("20.00")
    nxt = advance_reload(offer, date(2026, 8, 30))
    assert nxt == date(2026, 9, 6)
    assert offer.next_reload_on == date(2026, 9, 6)
    session.close()


def test_log_normal_unmatched_and_matched_acca(tmp_path):
    session = _session(tmp_path)
    sky = session.scalars(select(Account).where(Account.name == "Sky Bet")).one()
    smarkets = session.scalars(select(Account).where(Account.name == "Smarkets")).one()
    from app.calculator import calculate, unmatched_back

    lone = unmatched_back(BetType.NORMAL, 10, 3)
    session.add(
        Bet(
            event="Sunday single",
            market="Match odds / Home",
            bet_type=BetType.NORMAL,
            bookie_id=sky.id,
            exchange_id=smarkets.id,
            back_stake=lone.back_stake,
            back_odds=lone.back_odds,
            lay_stake=lone.lay_stake,
            lay_odds=lone.lay_odds,
            commission_percent=Decimal("2"),
            cashback=Decimal("0"),
            liability=lone.liability,
            expected_profit=lone.expected_profit,
            expected_bookie_back=lone.if_back_wins.bookie,
            expected_exchange_back=lone.if_back_wins.exchange,
            expected_bookie_lay=lone.if_lay_wins.bookie,
            expected_exchange_lay=lone.if_lay_wins.exchange,
            status=BetStatus.PENDING,
        )
    )
    acca = calculate(BetType.QUALIFYING, 5, 8, "8.20", 2)
    session.add(
        Bet(
            event="Four-fold",
            market="A, B, C, D",
            bet_type=BetType.ACCA,
            bookie_id=sky.id,
            exchange_id=smarkets.id,
            back_stake=acca.back_stake,
            back_odds=acca.back_odds,
            lay_stake=acca.lay_stake,
            lay_odds=acca.lay_odds,
            commission_percent=acca.commission_percent,
            cashback=Decimal("0"),
            liability=acca.liability,
            expected_profit=acca.expected_profit,
            expected_bookie_back=acca.if_back_wins.bookie,
            expected_exchange_back=acca.if_back_wins.exchange,
            expected_bookie_lay=acca.if_lay_wins.bookie,
            expected_exchange_lay=acca.if_lay_wins.exchange,
            status=BetStatus.PENDING,
        )
    )
    session.commit()
    normal = session.scalars(select(Bet).where(Bet.event == "Sunday single")).one()
    fold = session.scalars(select(Bet).where(Bet.event == "Four-fold")).one()
    assert normal.bet_type == BetType.NORMAL
    assert normal.lay_stake == 0
    assert normal.expected_profit == 0
    assert normal.expected_bookie_back == Decimal("20.00")
    assert fold.bet_type == BetType.ACCA
    assert fold.lay_stake > 0
    session.close()


def test_snapshot_keeps_reload(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    session = _session(tmp_path)
    sky = session.scalars(select(Account).where(Account.name == "Sky Bet")).one()
    session.add(
        Offer(
            name="Paddy Power reload",
            type=OfferType.RELOAD,
            bookie_id=sky.id,
            reload_frequency="monthly",
            reload_stake=Decimal("10"),
            reload_reward=Decimal("5"),
            next_reload_on=date(2026, 9, 1),
        )
    )
    session.commit()
    payload = dump_snapshot(session)
    session.close()
    Session2 = init_db(tmp_path / "copy.db")
    other = Session2()
    apply_snapshot(other, payload)
    other.commit()
    copied = other.scalars(select(Offer).where(Offer.name == "Paddy Power reload")).one()
    assert copied.reload_frequency == "monthly"
    assert copied.reload_stake == Decimal("10.00")
    assert copied.next_reload_on == date(2026, 9, 1)
    other.close()


def test_advance_reload_sets_date():
    offer = Offer(name="x", type=OfferType.RELOAD, bookie_id=1, reload_frequency="daily")
    nxt = advance_reload(offer, date(2026, 8, 30))
    assert nxt == date(2026, 8, 31)
    assert offer.next_reload_on == nxt


def test_create_reload_via_form(tmp_path, monkeypatch):
    root = tmp_path / "root"
    monkeypatch.setenv("MBD_ROOT", str(root))
    import app

    monkeypatch.setattr(app, "ROOT_DIR", root)
    monkeypatch.setattr(app, "DATA_DIR", root / "data")
    monkeypatch.setattr(app, "DB_PATH", root / "data" / "app.db")
    client = app.create_app().test_client()
    calc = client.get("/calculator")
    assert calc.status_code == 200
    assert b"Accumulator" in calc.data
    assert b"Bet builder" in calc.data
    assert b"Normal / unmatched" in calc.data
    assert b"offer_reload_frequency" in calc.data
    assert b"data-offer-field" in calc.data
    offers_page = client.get("/offers")
    assert b"How often" in offers_page.data
    assert b"js-offer-type" in offers_page.data
    assert b"You deposited" in offers_page.data
    import app.db as db

    session = db.SessionLocal()
    sky = session.scalars(select(Account).where(Account.name == "Sky Bet")).one()
    sky_id = sky.id
    session.close()
    created = client.post(
        "/offers",
        data={
            "name": "Form reload",
            "bookie_id": str(sky_id),
            "type": "reload",
            "deposit_amount": "0",
            "free_funds": "5",
            "reload_frequency": "weekly",
            "reload_stake": "20",
            "reload_reward": "5",
            "next_reload_on": "30/08/2026",
        },
        follow_redirects=True,
    )
    assert created.status_code == 200
    assert b"Form reload" in created.data
    session = db.SessionLocal()
    offer = session.scalars(select(Offer).where(Offer.name == "Form reload")).one()
    assert offer.reload_frequency == "weekly"
    assert offer.reload_stake == Decimal("20.00")
    offer_id = offer.id
    session.close()
    claimed = client.post(f"/offers/{offer_id}/claim-reload", follow_redirects=True)
    assert claimed.status_code == 200
    session = db.SessionLocal()
    offer = session.get(Offer, offer_id)
    assert offer.next_reload_on == date.today() + timedelta(days=7)
    session.close()
    welcome = client.post(
        "/offers",
        data={
            "name": "Form welcome",
            "bookie_id": str(sky_id),
            "type": "welcome",
            "deposit_amount": "10",
            "free_funds": "20",
            "reload_frequency": "weekly",
            "reload_stake": "20",
            "reload_reward": "5",
        },
        follow_redirects=True,
    )
    assert welcome.status_code == 200
    session = db.SessionLocal()
    offer = session.scalars(select(Offer).where(Offer.name == "Form welcome")).one()
    assert offer.reload_frequency == ""
    assert offer.deposit_amount == Decimal("10.00")
    session.close()
