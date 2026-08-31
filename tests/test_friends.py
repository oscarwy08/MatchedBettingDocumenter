from app.friends import (
    apply_account,
    create_invite,
    decrypt_view,
    encrypt_view,
    export_account,
    invite_by_secret,
    is_viewer_secret,
    load_cache,
    parse_friend_code,
    store_cache,
)
from app.sync import authorize_device, ensure_state, start_share


def test_invite_and_friend_token_cannot_write(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    ensure_state()
    invite = create_invite("Sam")
    secret = invite["secret"]
    assert is_viewer_secret(secret)
    assert is_viewer_secret("view." + secret)
    assert invite_by_secret("view." + secret)["id"] == invite["id"]
    assert not authorize_device("view." + secret)
    assert not authorize_device(secret)
    pin = start_share()
    assert authorize_device(pin)
    assert not authorize_device("view." + secret)


def test_export_apply_account(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    create_invite("Sam")
    blob = export_account()
    assert blob["invites"][0]["nickname"] == "Sam"
    apply_account({"account_name": "House", "invites": [], "friends": [{"id": "ab", "secret": "x" * 20, "nickname": "Alex"}]})
    again = export_account()
    assert again["account_name"] == "House"
    assert again["invites"] == []
    assert again["friends"][0]["nickname"] == "Alex"


def test_encrypt_round_trip():
    payload = {"stats": {"net_profit": "1.50"}, "recent_bets": []}
    blob = encrypt_view("super-secret", payload)
    assert blob.startswith("mbd1.")
    assert decrypt_view("super-secret", blob) == payload


def test_parse_friend_code():
    secret, hosts = parse_friend_code("view.abcDEF1234567890@10.0.0.2:5050+8.8.8.8:5050")
    assert secret == "abcDEF1234567890"
    assert hosts[0] == "10.0.0.2:5050"
    assert hosts[1] == "8.8.8.8:5050"
    secret, hosts = parse_friend_code("view.abcDEF1234567890xyz")
    assert secret == "abcDEF1234567890xyz"
    assert hosts == []


def test_friend_opens_own_dashboard_page(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    from app import create_app
    from app.friends import upsert_friend

    dto = {
        "nickname": "Alex",
        "stats": {
            "net_profit": "4.00",
            "pending_expected": "1.00",
            "bankroll": "20.00",
            "open_liability": "10.00",
            "month_profit": "2.00",
            "pending_count": 1,
            "settled_count": 0,
        },
        "profit_by_bookie": [{"id": 1, "name": "Sky Bet", "deposited": "10.00", "net_profit": "4.00", "bookie_profit": "2.00", "exchange_profit": "2.00", "balance": "14.00"}],
        "offers": [
            {
                "id": 3,
                "name": "Sky weekly",
                "bookie": "Sky Bet",
                "bookie_id": 1,
                "type": "reload",
                "status": "Reload due",
                "net_profit": "1.00",
                "deposited": "10.00",
                "bookie_profit": "1.00",
                "exchange_profit": "0.00",
                "free_funds": "5.00",
                "free_funds_used": "0.00",
                "pending_count": 1,
                "leg_count": 1,
                "reload_frequency": "weekly",
                "reload_stake": "20.00",
                "reload_reward": "5.00",
                "next_reload_on": "30/08/2026",
                "reload_due": True,
            }
        ],
        "bets": [
            {
                "id": 7,
                "event": "Friend cup",
                "bookie": "Sky Bet",
                "bookie_id": 1,
                "exchange": "Smarkets",
                "exchange_id": 2,
                "offer": "Sky weekly",
                "offer_id": 3,
                "bet_type": "qualifying",
                "status": "pending",
                "pending": True,
                "placed": "01/08/2026 11:30",
                "date": "01/08/2026 11:30",
                "starts": "01/08/2026 15:00",
                "back_stake": "10.00",
                "back_odds": "2.00",
                "lay_stake": "9.62",
                "lay_odds": "2.10",
                "liability": "10.58",
                "expected_profit": "-0.58",
                "expected_bookie_back": "10.00",
                "expected_exchange_back": "-10.58",
                "expected_bookie_lay": "-10.00",
                "expected_exchange_lay": "9.43",
                "actual_profit": "",
            }
        ],
        "recent_bets": [],
        "accounts": [
            {
                "id": 1,
                "name": "Sky Bet",
                "type": "bookie",
                "is_bookie": True,
                "commission_percent": "0",
                "priority": False,
                "restriction": "",
                "notes": "",
                "check_weekday": None,
                "last_checked_on": "30/08/2026",
                "opening": "0.00",
                "deposited": "10.00",
                "withdrawn": "0.00",
                "bookie_profit": "2.00",
                "exchange_profit": "2.00",
                "net_profit": "4.00",
                "balance": "14.00",
                "bets": 1,
                "offers": 1,
                "health": {"level": "green", "label": "Healthy", "percent": 100, "last_mug_on": "", "checked_today": True},
                "spark": {"points": "0,21 100,21", "area": "0,42 0,21 100,21 100,42", "down": False},
                "tasks": [],
            },
            {
                "id": 2,
                "name": "Smarkets",
                "type": "exchange",
                "is_bookie": False,
                "commission_percent": "2",
                "priority": False,
                "restriction": "",
                "notes": "",
                "check_weekday": None,
                "last_checked_on": "",
                "opening": "0.00",
                "deposited": "20.00",
                "withdrawn": "0.00",
                "bookie_profit": "0.00",
                "exchange_profit": "2.00",
                "net_profit": "2.00",
                "balance": "22.00",
                "bets": 1,
                "offers": 0,
                "health": None,
                "spark": {"points": "0,21 100,21", "area": "0,42 0,21 100,21 100,42", "down": False},
                "tasks": [],
            },
        ],
        "transfers": [
            {
                "id": 9,
                "account_id": 1,
                "account": "Sky Bet",
                "kind": "deposit",
                "amount": "10.00",
                "date": "01/08/2026",
                "notes": "Open",
                "offer_id": 3,
                "offer": "Sky weekly",
            }
        ],
        "today": {
            "today": "30/08/2026",
            "target": 10,
            "checked_count": 1,
            "clean": True,
            "routine": [
                {
                    "account_id": 1,
                    "name": "Sky Bet",
                    "health": {"level": "green", "label": "Healthy", "percent": 100},
                    "checked_today": True,
                    "priority": False,
                    "restriction": "",
                    "notes": "",
                    "reload_due": True,
                    "tasks_due": False,
                }
            ],
            "specials": [
                {
                    "kind": "reload",
                    "account_id": 1,
                    "account": "Sky Bet",
                    "name": "Sky weekly",
                    "detail": "30/08/2026",
                    "offer_id": 3,
                }
            ],
            "week": [{"label": "Sun", "count": 1, "today": True, "future": False, "href": "30/08/2026"}],
        },
        "charts": {
            "profit_time": {"title": "Profit over time", "kind": "area", "total": 4, "pending": -0.58, "labels": ["Sat"], "values": [4]},
            "by_bookie": {"title": "By bookie", "kind": "bar", "total": 4, "labels": ["Sky Bet"], "values": [4]},
        },
    }
    monkeypatch.setattr("app.friends.fetch_live", lambda _friend: dto)
    upsert_friend({"id": "f1", "secret": "x" * 20, "nickname": "Alex"})
    client = create_app().test_client()
    listing = client.get("/friends")
    assert listing.status_code == 200
    assert b"Alex" in listing.data
    assert b"Friend cup" not in listing.data
    dash = client.get("/friends/f1")
    assert dash.status_code == 200
    assert b"Back to friends" in dash.data
    assert b"Friend cup" in dash.data
    assert b"Dashboard" in dash.data
    assert b"Accounts" in dash.data
    assert b"Sky weekly" in dash.data
    assert b"Weekly" in dash.data
    assert b"Online" in dash.data
    listing_live = client.get("/friends")
    assert listing_live.status_code == 200
    assert b"Online" in listing_live.data
    bets_page = client.get("/friends/f1/bets")
    assert bets_page.status_code == 200
    assert b"All bets" in bets_page.data
    assert b"By event" in bets_page.data
    assert b"/friends/f1/offers/3" in bets_page.data
    assert b"Sky weekly" in bets_page.data
    assert b"<th>Placed</th>" in dash.data
    assert b"<th>Starts</th>" in dash.data
    assert b"<th>Placed</th>" in bets_page.data
    assert b"<th>Starts</th>" in bets_page.data
    assert b"01/08/2026 15:00" in bets_page.data
    events_page = client.get("/friends/f1/bets?view=events")
    assert events_page.status_code == 200
    assert b"event-card" in events_page.data
    assert b"Friend cup" in events_page.data
    assert b"Worst" in events_page.data
    assert b"Best" in events_page.data
    offers_page = client.get("/friends/f1/offers")
    assert offers_page.status_code == 200
    assert b"Sky weekly" in offers_page.data
    accounts = client.get("/friends/f1/accounts")
    assert accounts.status_code == 200
    assert b"Sky Bet" in accounts.data
    assert b"Smarkets" in accounts.data
    account = client.get("/friends/f1/accounts/1")
    assert account.status_code == 200
    assert b"Mug health" in account.data
    assert b"Friend cup" in account.data
    assert b"<th>Placed</th>" in account.data
    assert b"<th>Starts</th>" in account.data
    today = client.get("/friends/f1/today")
    assert today.status_code == 200
    assert b"Routine checks" in today.data
    vis = client.get("/friends/f1/visualiser")
    assert vis.status_code == 200
    offer = client.get("/friends/f1/offers/3")
    assert offer.status_code == 200
    assert b"All offers" in offer.data
    assert b"20.00" in offer.data or b"20" in offer.data
    assert b"<th>Placed</th>" in offer.data
    assert b"<th>Starts</th>" in offer.data
    bet = client.get("/friends/f1/bets/7")
    assert bet.status_code == 200
    assert b"All bets" in bet.data
    assert b"2.00" in bet.data


def test_friend_dash_survives_old_view_without_expected_profit(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    from app import create_app
    from app.friends import upsert_friend

    dto = {
        "nickname": "Alex",
        "stats": {
            "net_profit": "4.00",
            "pending_expected": "1.00",
            "bankroll": "20.00",
            "open_liability": "10.00",
            "month_profit": "2.00",
            "pending_count": 1,
            "settled_count": 0,
        },
        "profit_by_bookie": [{"name": "Sky Bet", "deposited": "10.00", "net_profit": "4.00"}],
        "recent_bets": [
            {
                "date": "01/08/2026 11:30",
                "event": "Old cup",
                "bookie": "Sky Bet",
                "status": "pending",
                "profit": "-0.58",
                "pending": True,
            }
        ],
    }
    monkeypatch.setattr("app.friends.fetch_live", lambda _friend: dto)
    upsert_friend({"id": "f1", "secret": "x" * 20, "nickname": "Alex"})
    client = create_app().test_client()
    dash = client.get("/friends/f1")
    assert dash.status_code == 200
    assert b"Old cup" in dash.data
    assert b"0.58" in dash.data


def test_money_filters_ignore_junk(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    from jinja2.runtime import Undefined

    from app import create_app

    filters = create_app().jinja_env.filters
    assert filters["pnl"](Undefined()) == "pnl-zero"
    assert filters["gbp"](Undefined()) == "–"
    assert filters["gbp"]("not-a-number") == "–"
    assert filters["pnl"]("not-a-number") == "pnl-zero"
    assert filters["pnl"]("-1.50") == "pnl-neg"
    assert filters["gbp"]("1.5") == "£1.50"


def test_last_available_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    dto = {"nickname": "Alex", "stats": {"net_profit": "4.00"}, "recent_bets": [], "profit_by_bookie": []}
    store_cache("abc123", dto)
    cached = load_cache("abc123")
    assert cached["payload"]["stats"]["net_profit"] == "4.00"
    assert cached["fetched_at"]
    assert cached["live_at"]


def test_presence_online_and_last_seen(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    from datetime import datetime, timedelta

    from app.friends import cache_path, presence_for

    store_cache("abc123", {"nickname": "Alex"})
    assert presence_for("abc123")["label"] == "Online"
    assert presence_for("abc123", live=True)["label"] == "Online"
    cached = load_cache("abc123")
    cached["live_at"] = (datetime.now() - timedelta(hours=3)).isoformat(timespec="seconds")
    cached["fetched_at"] = cached["live_at"]
    cache_path("abc123").write_text(__import__("json").dumps(cached), encoding="utf-8")
    row = presence_for("abc123", live=False)
    assert row["online"] is False
    assert row["label"].startswith("Last seen")
    assert presence_for("missing")["label"] == ""


def test_view_dto_is_read_only_dashboard(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    from datetime import date, datetime
    from decimal import Decimal

    from sqlalchemy import select

    from app.db import init_db
    from app.friends import bet_from_view, view_dto
    from app.models import Account, Bet, BetStatus, BetType, Offer, OfferType
    from app.seed import seed_accounts

    Session = init_db(tmp_path / "app.db")
    session = Session()
    seed_accounts(session)
    sky = session.scalars(select(Account).where(Account.name == "Sky Bet")).one()
    smarkets = session.scalars(select(Account).where(Account.name == "Smarkets")).one()
    session.add(
        Offer(
            name="Sky weekly",
            type=OfferType.RELOAD,
            bookie_id=sky.id,
            reload_frequency="weekly",
            reload_stake=Decimal("20"),
            reload_reward=Decimal("5"),
            next_reload_on=date(2026, 8, 30),
        )
    )
    session.add(
        Bet(
            event="Friend cup",
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
            placed_at=datetime(2026, 8, 1, 11, 30),
            starts_at=datetime(2026, 8, 1, 15, 0),
        )
    )
    session.commit()
    dto = view_dto(session, nickname="House")
    session.close()
    assert dto["nickname"] == "House"
    assert "stats" in dto
    assert dto["bets"]
    assert dto["offers"]
    assert dto["offers"][0]["reload_frequency"] == "weekly"
    assert dto["offers"][0]["reload_due"] is True
    assert dto["bets"][0]["event"] == "Friend cup"
    assert dto["bets"][0]["starts"] == "01/08/2026 15:00"
    assert dto["bets"][0]["starts_at"] == "2026-08-01T15:00"
    assert str(dto["bets"][0]["back_odds"]).startswith("2")
    assert dto["bets"][0]["bookie_id"] == sky.id
    assert dto["bets"][0]["offer_id"] is None
    assert dto["accounts"]
    assert any(row["name"] == "Sky Bet" and row["is_bookie"] for row in dto["accounts"])
    assert isinstance(dto["transfers"], list)
    assert dto["today"]["target"]
    assert "profit_time" in dto["charts"]
    found = bet_from_view(dto, dto["bets"][0]["id"])
    assert found["bookie"] == "Sky Bet"
