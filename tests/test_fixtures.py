import json
from datetime import datetime

from sqlalchemy import select

from app.models import Account, Bet, BetStatus, BetType
from app.notify import sweep
from app.settings import save


def _root(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    import app

    monkeypatch.setattr(app, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(app, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "data" / "app.db")
    monkeypatch.setattr("app.fixtures.RACING_GAP", 0)


def test_search_football_and_racing(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    from app.fixtures import is_finished, refresh, save_tokens, search

    save_tokens(football_token_value="tok", racing_user="user", racing_password="pass")

    def fake_get(url, headers=None):
        if "football-data.org" in url:
            return {
                "matches": [
                    {
                        "id": 42,
                        "utcDate": "2026-08-31T14:00:00Z",
                        "status": "TIMED",
                        "homeTeam": {"shortName": "Liverpool", "name": "Liverpool FC"},
                        "awayTeam": {"shortName": "Chelsea", "name": "Chelsea FC"},
                        "competition": {"name": "Premier League"},
                    }
                ]
            }
        if "racecards/free" in url:
            tomorrow = "day=tomorrow" in url
            return {
                "racecards": [
                    {
                        "race_id": "rac_tmw" if tomorrow else "rac_1",
                        "course": "Ascot",
                        "race_name": "Handicap",
                        "off_dt": (
                            "2026-09-01T14:50:00+01:00" if tomorrow else "2026-08-31T14:50:00+01:00"
                        ),
                        "region": "GB",
                    },
                    {
                        "race_id": "rac_york",
                        "course": "York",
                        "race_name": "Stakes",
                        "date": "2026-09-01",
                        "off_time": "3:15",
                        "off_dt": "",
                        "region": "GB",
                    }
                    if tomorrow
                    else {
                        "race_id": "rac_hk",
                        "course": "Sha Tin",
                        "race_name": "Overseas",
                        "off_dt": "2026-08-31T05:00:00+00:00",
                        "region": "HK",
                    },
                ]
            }
        if "results/today/free" in url:
            return {"results": [{"race_id": "rac_1"}], "total": 1, "limit": 100, "skip": 0}
        return None

    monkeypatch.setattr("app.fixtures._get_json", fake_get)
    football = search("liverpool", now=datetime(2026, 8, 31, 10, 0))
    assert football
    assert football[0]["label"] == "Liverpool vs Chelsea"
    assert football[0]["source"] == "football"
    assert football[0]["fixture_id"] == "42"
    assert football[0]["starts_at"]
    assert football[0]["ends_at"]
    racing = search("ascot", now=datetime(2026, 8, 31, 10, 0))
    assert racing
    assert racing[0]["source"] == "racing"
    assert "Ascot" in racing[0]["label"]
    labels = [row["label"] for row in search("ascot", now=datetime(2026, 8, 31, 10, 0))]
    assert any(label.startswith("14:50") for label in labels)
    assert any("Tomorrow" in label for label in labels)
    york = search("york", now=datetime(2026, 8, 31, 10, 0))
    assert york
    assert york[0]["fixture_id"] == "rac_york"
    assert "Tomorrow" in york[0]["label"]
    assert york[0]["starts_at"].endswith("15:15")
    assert search("tomorrow", now=datetime(2026, 8, 31, 10, 0))
    assert not search("sha tin", now=datetime(2026, 8, 31, 10, 0))
    assert not is_finished("football", "42")
    refresh(now=datetime(2026, 8, 31, 15, 0), racing_results=True)
    assert is_finished("racing", "rac_1")
    assert not is_finished("racing", "rac_hk")


def test_finished_football_status(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    from app.fixtures import cache_path, is_finished

    cache_path().write_text(
        json.dumps(
            {
                "items": [
                    {
                        "source": "football",
                        "fixture_id": "42",
                        "status": "FINISHED",
                        "label": "Liverpool vs Chelsea",
                        "starts_at": "2026-08-31T15:00",
                    }
                ],
                "racing_finished": [],
            }
        ),
        encoding="utf-8",
    )
    assert is_finished("football", "42")
    assert not is_finished("football", "99")


def test_save_tokens_keep_password(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    from app.fixtures import configured, racing_creds, save_tokens

    save_tokens(football_token_value="abc", racing_user="user", racing_password="secret")
    save_tokens(football_token_value="", racing_user="user", racing_password="")
    user, password = racing_creds()
    assert user == "user"
    assert password == "secret"
    assert configured() == {"football": True, "racing": True}


def test_api_and_log_fixture(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    monkeypatch.setattr("app.desktop_notify.send", lambda *a, **k: True)
    import app

    client = app.create_app().test_client()
    from app.fixtures import save_tokens

    save_tokens(football_token_value="tok", racing_user="", racing_password="")
    monkeypatch.setattr(
        "app.fixtures._get_json",
        lambda url, headers=None: {
            "matches": [
                {
                    "id": 7,
                    "utcDate": "2026-08-31T14:00:00Z",
                    "status": "TIMED",
                    "homeTeam": {"shortName": "Arsenal", "name": "Arsenal FC"},
                    "awayTeam": {"shortName": "Spurs", "name": "Tottenham Hotspur FC"},
                    "competition": {"name": "Premier League"},
                }
            ]
        }
        if "football-data.org" in url
        else None,
    )
    api = client.get("/api/fixtures?q=arsenal")
    assert api.status_code == 200
    payload = api.get_json()
    assert payload["configured"]["football"] is True
    assert payload["items"][0]["label"] == "Arsenal vs Spurs"
    settings = client.get("/settings")
    assert b"Event list" in settings.data
    assert b"football-data.org" in settings.data
    session = app.db.SessionLocal()
    bookie = session.scalars(select(Account).where(Account.name == "Betfred")).one()
    exchange = session.scalars(select(Account).where(Account.name == "Smarkets")).one()
    session.close()
    logged = client.post(
        "/calculator/log",
        data={
            "bet_type": "normal",
            "back_stake": "10",
            "back_odds": "2",
            "lay_odds": "2.1",
            "commission_percent": "2",
            "cashback": "0",
            "bookie_id": str(bookie.id),
            "exchange_id": str(exchange.id),
            "date_placed": "2026-08-31",
            "starts_at": "2026-08-31T15:00",
            "ends_at": "2026-08-31T17:05",
            "event": "Arsenal vs Spurs",
            "fixture_source": "football",
            "fixture_id": "7",
        },
        follow_redirects=True,
    )
    assert logged.status_code == 200
    session = app.db.SessionLocal()
    saved = session.scalars(select(Bet).where(Bet.event == "Arsenal vs Spurs")).one()
    assert saved.fixture_source == "football"
    assert saved.fixture_id == "7"
    assert saved.ends_at == datetime(2026, 8, 31, 17, 5)
    session.close()


def test_sweep_event_finished_once(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    monkeypatch.setattr("app.desktop_notify.send", lambda *a, **k: True)
    monkeypatch.setattr("app.fixtures.refresh", lambda **k: None)
    monkeypatch.setattr(
        "app.fixtures.is_finished",
        lambda source, ident: source == "football" and ident == "42",
    )
    import app

    app.create_app()
    save({"last_sites_checked_on": "2026-08-30"})
    session = app.db.SessionLocal()
    bookie = session.scalars(select(Account).where(Account.name == "Betfred")).one()
    exchange = session.scalars(select(Account).where(Account.name == "Smarkets")).one()
    session.add(
        Bet(
            event="Liverpool vs Chelsea",
            bet_type=BetType.NORMAL,
            bookie_id=bookie.id,
            exchange_id=exchange.id,
            back_stake=10,
            back_odds=2,
            lay_stake=10,
            lay_odds=2.1,
            commission_percent=2,
            cashback=0,
            liability=11,
            expected_profit=0,
            expected_bookie_back=10,
            expected_exchange_back=-9,
            expected_bookie_lay=-10,
            expected_exchange_lay=9.8,
            status=BetStatus.PENDING,
            starts_at=datetime(2026, 8, 30, 15, 0),
            fixture_source="football",
            fixture_id="42",
        )
    )
    session.commit()
    first = sweep(session, now=datetime(2026, 8, 30, 17, 30), send_desktop=False)
    kinds = {row.kind for row in first}
    assert "bet_ended" in kinds
    assert "bet_started" in kinds
    second = sweep(session, now=datetime(2026, 8, 30, 17, 30), send_desktop=False)
    assert second == []
    session.close()
