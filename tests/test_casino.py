from decimal import Decimal

from sqlalchemy import select

from app.casino import actuals, bonus_ev, offer_ev, playthrough_cost, qualifying_ev, spins_ev
from app.models import Account, Bet, BetStatus, BetType, Offer, OfferType
from app.services import offer_snapshot
from app.snapshot import apply_snapshot, dump_snapshot
from app.db import init_db


def D(value) -> Decimal:
    return Decimal(str(value))


def test_jackpotjoy_qualifying_and_spins():
    assert qualifying_ev(10, "96.02") == D("-0.40")
    spins = spins_ev(30, "0.20", "96.02")
    assert spins["face"] == D("6.00")
    assert spins["gross"] == D("5.76")
    assert spins["cash"] == D("5.76")
    combined = offer_ev(
        casino_wager=10,
        casino_rtp="96.02",
        spin_count=30,
        spin_value="0.20",
        spin_rtp="96.02",
    )
    assert combined["qualifying"] == D("-0.40")
    assert combined["spins"] == D("5.76")
    assert combined["net"] == D("5.36")


def test_bonus_with_wagering_matches_blog_intro():
    assert bonus_ev(20, 5, "97.3") == D("17.30")
    assert playthrough_cost(100, "97.3") == D("2.70")


def test_free_spins_wagering_and_cap():
    spins = spins_ev(50, "0.10", 96, wagering=10, max_cashout=3, clear_rtp=96)
    assert spins["gross"] == D("4.80")
    # 4.80 × 10 = £48 playthrough at 4% house edge → £1.92 cost → £2.88, capped at £3
    assert spins["cash"] == D("2.88")
    capped = spins_ev(50, "0.10", 96, wagering=0, max_cashout="2.00")
    assert capped["cash"] == D("2.00")


def _client(tmp_path, monkeypatch):
    root = tmp_path / "root"
    monkeypatch.setenv("MBD_ROOT", str(root))
    import app

    monkeypatch.setattr(app, "ROOT_DIR", root)
    monkeypatch.setattr(app, "DATA_DIR", root / "data")
    monkeypatch.setattr(app, "DB_PATH", root / "data" / "app.db")
    client = app.create_app().test_client()
    import app.db as db

    return client, db


def test_calculator_api_jackpotjoy_legs(tmp_path, monkeypatch):
    client, _db = _client(tmp_path, monkeypatch)
    calc = client.get("/calculator")
    assert calc.status_code == 200
    assert b"Casino wager" in calc.data
    assert b"Free spins" in calc.data
    assert b"id=\"rtp-field\"" in calc.data
    assert b"Casino bonus" in calc.data

    wager = client.post(
        "/api/calculate",
        json={"bet_type": "casino_wager", "back_stake": "10", "rtp": "96.02"},
    )
    assert wager.status_code == 200
    body = wager.get_json()
    assert body["expected_profit"] == "-0.40"
    assert body["expected_return"] == "9.60"

    spins = client.post(
        "/api/calculate",
        json={
            "bet_type": "free_spins",
            "spin_count": "30",
            "spin_value": "0.20",
            "rtp": "96.02",
            "wagering_multiplier": "0",
        },
    )
    assert spins.status_code == 200
    body = spins.get_json()
    assert body["back_stake"] == "6.00"
    assert body["expected_profit"] == "5.76"
    assert body["expected_return"] == "5.76"


def test_create_free_spins_offer_log_and_settle(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    session = db.SessionLocal()
    bookie = session.scalars(select(Account).where(Account.name == "LeoVegas")).one()
    bookie_id = bookie.id
    session.close()

    created = client.post(
        "/offers",
        data={
            "name": "Jackpotjoy 30 spins",
            "bookie_id": str(bookie_id),
            "type": "free_spins",
            "deposit_amount": "10",
            "casino_wager": "10",
            "casino_rtp": "96.02",
            "spin_count": "30",
            "spin_value": "0.20",
            "spin_game": "Double Bubble",
            "spin_rtp": "96.02",
            "wagering_multiplier": "0",
            "max_cashout": "0",
        },
        follow_redirects=True,
    )
    assert created.status_code == 200
    assert b"Expected EV" in created.data
    assert "£5.36".encode() in created.data
    assert b"Double Bubble" in created.data

    session = db.SessionLocal()
    offer = session.scalars(select(Offer).where(Offer.name == "Jackpotjoy 30 spins")).one()
    assert offer.type == OfferType.FREE_SPINS
    assert offer.spin_count == 30
    assert offer.spin_value == D("0.20")
    assert offer.free_funds == D("6.00")
    snap = offer_snapshot(offer)
    assert snap["expected_ev"] == D("5.36")
    assert snap["is_casino"] is True
    offer_id = offer.id
    session.close()

    calc = client.get(f"/calculator?offer_id={offer_id}")
    assert calc.status_code == 200
    assert b'value="casino_wager" selected' in calc.data or b"casino_wager" in calc.data
    assert b'data-casino-offer="1"' in calc.data
    assert b"Cashed out" in calc.data
    assert b"Profit" in calc.data
    assert b"0.20" in calc.data or b"10.00" in calc.data

    logged = client.post(
        "/calculator/log",
        data={
            "bet_type": "casino_wager",
            "back_stake": "10",
            "rtp": "96.02",
            "bookie_id": str(bookie_id),
            "offer_id": str(offer_id),
            "date_placed": "2026-09-01",
            "event": "Jackpotjoy qualifying",
            "market": "Double Bubble",
        },
        follow_redirects=True,
    )
    assert logged.status_code == 200
    session = db.SessionLocal()
    wager = session.scalars(select(Bet).where(Bet.event == "Jackpotjoy qualifying")).one()
    assert wager.bet_type == BetType.CASINO_WAGER
    assert wager.expected_profit == D("-0.40")
    assert wager.lay_stake == D("0.00")
    assert wager.rtp == D("96.020") or wager.rtp == D("96.02")
    wager_id = wager.id
    session.close()

    spins_log = client.post(
        "/calculator/log",
        data={
            "bet_type": "free_spins",
            "back_stake": "0.20",
            "spin_count": "30",
            "rtp": "96.02",
            "wagering_multiplier": "0",
            "bookie_id": str(bookie_id),
            "offer_id": str(offer_id),
            "date_placed": "2026-09-01",
            "event": "Jackpotjoy free spins",
            "market": "Double Bubble",
        },
        follow_redirects=True,
    )
    assert spins_log.status_code == 200
    session = db.SessionLocal()
    spins = session.scalars(select(Bet).where(Bet.event == "Jackpotjoy free spins")).one()
    assert spins.bet_type == BetType.FREE_SPINS
    assert spins.back_stake == D("6.00")
    assert spins.spin_count == 30
    assert spins.expected_profit == D("5.76")
    spins_id = spins.id
    session.close()

    detail = client.get(f"/bets/{wager_id}")
    assert detail.status_code == 200
    assert b"Cashed out" in detail.data
    assert b"Profit" in detail.data
    assert b"Finished" in detail.data
    assert b"Lay won" not in detail.data

    settled = client.post(
        f"/bets/{wager_id}/settle",
        data={"outcome": "back_won", "actual_profit": "0.50"},
        follow_redirects=True,
    )
    assert settled.status_code == 200
    session = db.SessionLocal()
    wager = session.get(Bet, wager_id)
    assert wager.status == BetStatus.BACK_WON
    assert wager.actual_profit == D("0.50")
    assert wager.actual_bookie_profit == D("0.50")
    assert wager.actual_exchange_profit == D("0.00")
    session.close()

    client.post(
        f"/bets/{spins_id}/settle",
        data={"outcome": "back_won", "actual_profit": "4.20"},
        follow_redirects=True,
    )
    session = db.SessionLocal()
    offer = session.get(Offer, offer_id)
    snap = offer_snapshot(offer)
    assert snap["net_profit"] == D("4.70")
    assert snap["status"] == "Used"
    payload = dump_snapshot(session)
    session.close()

    copy = init_db(tmp_path / "copy.db")()
    apply_snapshot(copy, payload)
    copy.commit()
    copied = copy.scalars(select(Offer).where(Offer.name == "Jackpotjoy 30 spins")).one()
    assert copied.spin_count == 30
    assert copied.spin_game == "Double Bubble"
    copy.close()


def test_casino_bonus_offer_completes_when_settled(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    session = db.SessionLocal()
    bookie = session.scalars(select(Account).where(Account.name == "Grosvenor")).one()
    bookie_id = bookie.id
    session.close()
    created = client.post(
        "/offers",
        data={
            "name": "Foxy £20 bonus",
            "bookie_id": str(bookie_id),
            "type": "casino",
            "deposit_amount": "20",
            "free_funds": "20",
            "wagering_multiplier": "5",
            "bonus_rtp": "97.3",
            "casino_wager": "0",
            "casino_rtp": "0",
        },
        follow_redirects=True,
    )
    assert created.status_code == 200
    assert "£17.30".encode() in created.data
    session = db.SessionLocal()
    offer = session.scalars(select(Offer).where(Offer.name == "Foxy £20 bonus")).one()
    assert offer.type == OfferType.CASINO
    assert offer_snapshot(offer)["expected_bonus"] == D("17.30")
    offer_id = offer.id
    session.close()

    client.post(
        "/calculator/log",
        data={
            "bet_type": "casino_wager",
            "back_stake": "100",
            "rtp": "97.3",
            "bookie_id": str(bookie_id),
            "offer_id": str(offer_id),
            "date_placed": "2026-09-01",
            "event": "Bonus clearing",
        },
        follow_redirects=True,
    )
    session = db.SessionLocal()
    bet = session.scalars(select(Bet).where(Bet.event == "Bonus clearing")).one()
    bet_id = bet.id
    session.close()
    client.post(
        f"/bets/{bet_id}/settle",
        data={"outcome": "back_won", "actual_profit": "16.80"},
        follow_redirects=True,
    )
    session = db.SessionLocal()
    offer = session.get(Offer, offer_id)
    assert offer.status == "Complete"
    session.close()


def test_actuals_from_cashout_or_profit():
    assert actuals("casino_wager", 10, cashout=50) == {"cashout": D("50.00"), "profit": D("40.00")}
    assert actuals("casino_wager", 10, profit=40) == {"cashout": D("50.00"), "profit": D("40.00")}
    assert actuals("casino_wager", 10, cashout=0) == {"cashout": D("0.00"), "profit": D("-10.00")}
    assert actuals("free_spins", 6, cashout="187.50") == {"cashout": D("187.50"), "profit": D("187.50")}
    assert actuals("free_spins", 6, profit=0) == {"cashout": D("0.00"), "profit": D("0.00")}


def test_log_slots_with_winnings(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    session = db.SessionLocal()
    bookie = session.scalars(select(Account).where(Account.name == "LeoVegas")).one()
    bookie_id = bookie.id
    session.close()
    logged = client.post(
        "/calculator/log",
        data={
            "bet_type": "free_spins",
            "back_stake": "0.20",
            "spin_count": "30",
            "rtp": "96.02",
            "bookie_id": str(bookie_id),
            "date_placed": "2026-09-01",
            "event": "Big hit",
            "market": "Big Bass",
            "casino_cashout": "240",
        },
        follow_redirects=True,
    )
    assert logged.status_code == 200
    session = db.SessionLocal()
    bet = session.scalars(select(Bet).where(Bet.event == "Big hit")).one()
    assert bet.status == BetStatus.BACK_WON
    assert bet.actual_profit == D("240.00")
    assert bet.casino_cashout == D("240.00")
    session.close()


def test_settle_playthrough_from_cashout(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    session = db.SessionLocal()
    bookie = session.scalars(select(Account).where(Account.name == "LeoVegas")).one()
    bookie_id = bookie.id
    session.close()
    client.post(
        "/calculator/log",
        data={
            "bet_type": "casino_wager",
            "back_stake": "10",
            "rtp": "96.02",
            "bookie_id": str(bookie_id),
            "date_placed": "2026-09-01",
            "event": "Playthrough",
        },
        follow_redirects=True,
    )
    session = db.SessionLocal()
    bet = session.scalars(select(Bet).where(Bet.event == "Playthrough")).one()
    bet_id = bet.id
    session.close()
    settled = client.post(
        f"/bets/{bet_id}/settle",
        data={"outcome": "back_won", "casino_cashout": "85"},
        follow_redirects=True,
    )
    assert settled.status_code == 200
    session = db.SessionLocal()
    bet = session.get(Bet, bet_id)
    assert bet.actual_profit == D("75.00")
    assert bet.casino_cashout == D("85.00")
    assert bet.actual_bookie_profit == D("75.00")
    detail = client.get(f"/bets/{bet_id}")
    assert b"Cashed out" in detail.data
    assert "£85.00".encode() in detail.data
    session.close()
