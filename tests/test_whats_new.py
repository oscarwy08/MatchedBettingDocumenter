from app.fixtures import football_token, racing_creds
from app.whats_new import current, mark_seen, pending


def _root(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    import app

    monkeypatch.setattr(app, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(app, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "data" / "app.db")


def test_ok_survives_relaunch(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    import app

    client = app.create_app().test_client()
    home = client.get("/")
    assert b"Selections on the calculator" in home.data
    assert b"inert" in home.data
    assert b'name="football_token"' not in home.data
    assert b">OK<" in home.data
    assert pending() is True
    skipped = client.post("/whats-new", data={"action": "ok", "next": "/calculator"}, follow_redirects=False)
    assert skipped.status_code == 302
    assert skipped.headers["Location"].endswith("/calculator")
    assert pending() is False
    later = client.get("/")
    assert b"Selections on the calculator" not in later.data
    assert b" inert" not in later.data
    relaunched = app.create_app().test_client()
    again = relaunched.get("/")
    assert b"Selections on the calculator" not in again.data
    assert pending() is False


def test_enter_requires_all_fields(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    monkeypatch.setattr("app.whats_new.VERSION", "2.0.0")
    import app

    client = app.create_app().test_client()
    home = client.get("/")
    assert b'data-whats-new-primary disabled' in home.data
    blocked = client.post(
        "/whats-new",
        data={
            "action": "save",
            "next": "/",
            "football_token": "tok-1",
            "racing_user": "user",
            "racing_password": "",
        },
        follow_redirects=False,
    )
    assert blocked.status_code == 302
    assert pending() is True
    assert football_token() == ""
    later = client.get("/")
    assert b"Pick football and racing events" in later.data


def test_enter_saves_tokens(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    monkeypatch.setattr("app.whats_new.VERSION", "2.0.0")
    import app

    client = app.create_app().test_client()
    saved = client.post(
        "/whats-new",
        data={
            "action": "save",
            "next": "https://evil.example/phish",
            "football_token": "tok-1",
            "racing_user": "user",
            "racing_password": "secret",
        },
        follow_redirects=False,
    )
    assert saved.status_code == 302
    assert saved.headers["Location"].endswith("/")
    assert football_token() == "tok-1"
    user, password = racing_creds()
    assert user == "user"
    assert password == "secret"
    assert pending() is False


def test_old_event_picker_flag_counts_as_seen(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    from app.whats_new import seen

    path = tmp_path / "data" / "whats_new.json"
    path.write_text('{"event_picker": true}\n', encoding="utf-8")
    assert "1.9.7" in seen()
    assert pending() is True
    assert current()["title"] == "Selections on the calculator"


def test_next_version_shows_ok_card(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    import app

    mark_seen("2.0.4")
    monkeypatch.setattr("app.whats_new.VERSION", "2.0.5")
    assert pending() is True
    note = current()
    assert note["title"] == "Version 2.0.5"
    assert note.get("fields") is None
    client = app.create_app().test_client()
    page = client.get("/")
    assert b"Version 2.0.5" in page.data
    assert b">OK<" in page.data
    assert b'name="football_token"' not in page.data
    client.post("/whats-new", data={"action": "ok", "next": "/"})
    assert pending() is False
    relaunched = app.create_app().test_client()
    assert b"Version 2.0.5" not in relaunched.get("/").data
