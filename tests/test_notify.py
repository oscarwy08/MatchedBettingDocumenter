from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select

from app.models import (
    Account,
    AccountTask,
    Bet,
    BetStatus,
    BetType,
    Notification,
    Offer,
    OfferType,
    ScheduleEvent,
)
from app.notify import list_notifications, mark_read, sweep
from app.settings import save


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    import app

    monkeypatch.setattr(app, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(app, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "data" / "app.db")
    monkeypatch.setattr("app.desktop_notify.send", lambda *a, **k: True)
    return app.create_app().test_client()


def _session():
    import app.db as db

    return db.SessionLocal()


def _accounts(session):
    bookie = session.scalars(select(Account).where(Account.name == "Betfred")).one()
    exchange = session.scalars(select(Account).where(Account.name == "Smarkets")).one()
    return bookie, exchange


def _bet_kwargs(bookie, exchange, **extra):
    values = dict(
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
        starts_at=datetime(2026, 8, 30, 19, 45),
    )
    values.update(extra)
    return values


def _quiet_scan(day=None):
    save({"last_sites_checked_on": (day or date(2026, 8, 30)).isoformat()})


def test_sweep_fires_each_kind_once(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _quiet_scan()
    session = _session()
    bookie, exchange = _accounts(session)
    session.add(Bet(**_bet_kwargs(bookie, exchange)))
    offer = Offer(
        name="Sky weekly",
        type=OfferType.RELOAD,
        bookie_id=bookie.id,
        reload_frequency="weekly",
        reload_stake=Decimal("20"),
        next_reload_on=date(2026, 8, 30),
    )
    session.add(offer)
    session.add(AccountTask(account_id=bookie.id, due_on=date(2026, 8, 30), note="Check odds"))
    session.add(ScheduleEvent(title="Sky Saturday", due_on=date(2026, 8, 30)))
    session.commit()
    now = datetime(2026, 8, 30, 20, 0)
    first = sweep(session, now=now, send_desktop=False)
    kinds = {row.kind for row in first}
    assert kinds == {"bet_started", "reload_due", "task_due", "event_due"}
    assert any("/bets/" in row.href for row in first)
    assert any("/offers/" in row.href for row in first)
    assert any("/accounts/" in row.href for row in first)
    assert any(row.href == "/today" for row in first)
    second = sweep(session, now=now, send_desktop=False)
    assert second == []
    session.close()
    client.get("/")


def test_settled_and_done_are_skipped(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch)
    _quiet_scan()
    session = _session()
    bookie, exchange = _accounts(session)
    session.add(
        Bet(
            **_bet_kwargs(
                bookie,
                exchange,
                status=BetStatus.BACK_WON,
                actual_profit=Decimal("1"),
            )
        )
    )
    session.add(AccountTask(account_id=bookie.id, due_on=date(2026, 8, 30), note="Done", done=True))
    session.add(ScheduleEvent(title="Done offer", due_on=date(2026, 8, 30), done=True))
    session.commit()
    created = sweep(session, now=datetime(2026, 8, 30, 20, 0), send_desktop=False)
    assert created == []
    session.close()


def test_future_start_waits(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch)
    _quiet_scan()
    session = _session()
    bookie, exchange = _accounts(session)
    session.add(Bet(**_bet_kwargs(bookie, exchange, starts_at=datetime(2026, 8, 30, 21, 0))))
    session.commit()
    created = sweep(session, now=datetime(2026, 8, 30, 20, 0), send_desktop=False)
    assert created == []
    later = sweep(session, now=datetime(2026, 8, 30, 21, 0), send_desktop=False)
    assert [row.kind for row in later] == ["bet_started"]
    session.close()


def test_site_scan_due(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch)
    save({"last_sites_checked_on": date(2026, 8, 20).isoformat()})
    session = _session()
    created = sweep(session, now=datetime(2026, 8, 30, 9, 0), send_desktop=False)
    assert any(row.kind == "site_scan" and row.href == "/today" for row in created)
    session.close()


def test_api_and_page_and_mark_read(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _quiet_scan()
    session = _session()
    bookie, exchange = _accounts(session)
    session.add(Bet(**_bet_kwargs(bookie, exchange)))
    session.commit()
    session.close()
    api = client.get("/api/notifications")
    assert api.status_code == 200
    payload = api.get_json()
    assert payload["unread"] == 1
    assert payload["items"][0]["href"].startswith("/bets/")
    assert payload["items"][0]["title"] == "Bet started"
    page = client.get("/notifications")
    assert page.status_code == 200
    assert b"Bet started" in page.data
    assert b"notify-bell" in page.data
    assert b"notify.js" in page.data
    assert b"notify-clear" in page.data
    assert b">Clear<" in page.data
    note_id = payload["items"][0]["id"]
    marked = client.post("/notifications/read", json={"id": note_id})
    assert marked.status_code == 200
    again = client.get("/api/notifications").get_json()
    assert again["unread"] == 0
    session = _session()
    row = session.get(Notification, note_id)
    assert row.read_at is not None
    session.close()
    settings = client.get("/settings")
    assert b"desktop_notifications" in settings.data
    assert b"Send a test notification" in settings.data


def test_mark_all_read(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch)
    _quiet_scan()
    session = _session()
    bookie, exchange = _accounts(session)
    session.add(Bet(**_bet_kwargs(bookie, exchange)))
    session.add(ScheduleEvent(title="Sky Saturday", due_on=date(2026, 8, 30)))
    session.commit()
    sweep(session, now=datetime(2026, 8, 30, 20, 0), send_desktop=False)
    unread = [row for row in list_notifications(session) if row.read_at is None]
    assert unread
    mark_read(session, all_items=True)
    assert all(row.read_at for row in list_notifications(session))
    session.close()


def test_windows_toast_uses_registered_app_id():
    from app.desktop_notify import _windows_script, _xml

    script = _windows_script("Bet started", "Kickoff is underway")
    assert "Windows.Data.Xml.Dom, ContentType" in script
    assert "CreateToastNotifier('Matched Betting Documenter')" not in script
    assert r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe" in script
    assert 'template="ToastGeneric"' in script
    assert "$xml.LoadXml(@'" in script
    assert "LoadXml('" not in script
    assert 'placement="attribution"' in script
    assert "ShowBalloonTip" in script
    assert _xml("A & B <C>") == "A &amp; B &lt;C&gt;"
    escaped = _windows_script("O'Brien", "It's on")
    assert "O&apos;Brien" in escaped
    assert "It&apos;s on" in escaped
    assert "It''s on" in escaped
    assert "O'Brien" not in escaped.split("ShowBalloonTip")[0]


def test_notify_test_sends_when_enabled(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    sent = []
    monkeypatch.setattr("app.desktop_notify.send", lambda title, body: sent.append((title, body)) or True)
    save({"desktop_notifications": True})
    response = client.post("/settings/notify-test")
    assert response.status_code == 302
    assert sent == [("Matched Betting Documenter", "Desktop notifications are working on this computer.")]
    save({"desktop_notifications": False})
    sent.clear()
    blocked = client.post("/settings/notify-test")
    assert blocked.status_code == 302
    assert sent == []


def test_notify_test_includes_error_detail(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr("app.desktop_notify.send", lambda *a, **k: False)
    monkeypatch.setattr("app.desktop_notify.last_error", lambda: "Windows PowerShell was not found.")
    save({"desktop_notifications": True})
    client.post("/settings/notify-test")
    page = client.get("/settings")
    assert b"Windows PowerShell was not found." in page.data


def test_clear_all_via_json(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _quiet_scan()
    session = _session()
    bookie, exchange = _accounts(session)
    session.add(Bet(**_bet_kwargs(bookie, exchange)))
    session.commit()
    session.close()
    assert client.get("/api/notifications").get_json()["unread"] == 1
    cleared = client.post("/notifications/read", json={"all": True})
    assert cleared.status_code == 200
    assert client.get("/api/notifications").get_json()["unread"] == 0
