import pytest

from app.settings import DEFAULTS, get, load, parse_port, save, settings_path


def test_defaults_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    loaded = load()
    assert loaded == DEFAULTS
    assert get("open_browser") is True
    assert not settings_path().exists()


def test_save_and_reload(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    saved = save({"open_browser": False, "allow_lan": False})
    assert saved["open_browser"] is False
    assert saved["update_on_start"] is True
    assert saved["update_popup"] is True
    assert saved["allow_lan"] is False
    assert saved["excel_sync"] is True
    assert saved["port"] == 5050
    assert saved["default_exchange_id"] is None
    assert load()["open_browser"] is False
    assert settings_path().is_file()


def test_unknown_keys_are_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    save({"open_browser": False, "not_a_setting": True})
    assert "not_a_setting" not in load()


def test_update_toggles_are_independent(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    save({"update_on_start": False, "update_popup": True})
    loaded = load()
    assert loaded["update_on_start"] is False
    assert loaded["update_popup"] is True


def test_port_exchange_and_excel(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    saved = save({"port": 6060, "default_exchange_id": 3, "excel_sync": False})
    assert saved["port"] == 6060
    assert saved["default_exchange_id"] == 3
    assert saved["excel_sync"] is False
    assert get("port") == 6060
    assert parse_port("5050") == 5050
    with pytest.raises(ValueError):
        parse_port("80")


def test_combined_auto_update_key_splits(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"auto_update": false}\n', encoding="utf-8")
    loaded = load()
    assert loaded["update_on_start"] is False
    assert loaded["update_popup"] is False
