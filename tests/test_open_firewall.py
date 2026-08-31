from app.open_firewall import apply, is_open, launch_elevated
from app.settings import save


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    import app

    monkeypatch.setattr(app, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(app, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "data" / "app.db")
    return app.create_app().test_client()


def test_apply_writes_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    monkeypatch.setattr("app.open_firewall.sys.platform", "linux")
    monkeypatch.setattr("app.linux_firewall.allow_port", lambda port: True)
    assert apply(5050) is True
    assert is_open(5050) is True
    assert (tmp_path / "data" / "firewall.ok").read_text(encoding="utf-8").strip() == "5050"


def test_settings_page_has_firewall_button(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    page = client.get("/settings")
    assert page.status_code == 200
    assert b"Allow this port through the firewall" in page.data
    assert b"allow-firewall.sh" not in page.data
    assert b"allow-firewall.bat" not in page.data


def test_settings_firewall_opens_without_prompt(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    save({"port": 5050})
    monkeypatch.setattr("app.open_firewall.apply", lambda port: True)
    launched = []
    monkeypatch.setattr("app.open_firewall.launch_elevated", lambda port: launched.append(port) or True)
    response = client.post("/settings/firewall")
    assert response.status_code == 302
    assert launched == []


def test_settings_firewall_elevates_when_needed(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    save({"port": 5050})
    monkeypatch.setattr("app.open_firewall.is_open", lambda port: False)
    monkeypatch.setattr("app.open_firewall.apply", lambda port: False)
    launched = []
    monkeypatch.setattr("app.open_firewall.launch_elevated", lambda port: launched.append(port) or True)
    response = client.post("/settings/firewall")
    assert response.status_code == 302
    assert launched == [5050]
