from app import live_update
from app.version import VERSION


def test_status_skips_network_in_git_checkout(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    result = live_update.status(refresh=True)
    assert result["dev"] is True
    assert result["available"] is False
    assert result["current"] == VERSION


def test_status_reports_newer_when_not_dev(tmp_path, monkeypatch):
    import update

    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    monkeypatch.setattr(update, "_is_dev_checkout", lambda: False)
    monkeypatch.setattr(update, "configured_repo", lambda: "owner/repo")
    monkeypatch.setattr(update, "fetch_published_version", lambda repo: "9.9.9")
    live_update._checked_at = 0.0
    live_update._latest = ""
    live_update._error = ""
    result = live_update.status(refresh=True)
    assert result["dev"] is False
    assert result["available"] is True
    assert result["latest"] == "9.9.9"


def test_status_off_when_popup_disabled(tmp_path, monkeypatch):
    from app.settings import save

    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    save({"update_popup": False})
    result = live_update.status(refresh=True)
    assert result["disabled"] is True
    assert result["available"] is False


def test_restart_command_waits_then_execs():
    cmd = live_update.restart_command("/venv/bin/python", "/app/run.py")
    assert cmd[0] == "/bin/sh"
    assert "sleep 1" in cmd[-1]
    assert "/venv/bin/python" in cmd[-1]
    assert "/app/run.py" in cmd[-1]


def test_restart_command_windows(monkeypatch):
    monkeypatch.setattr(live_update.os, "name", "nt")
    cmd = live_update.restart_command(r"C:\py.exe", r"C:\app\run.py")
    assert cmd[0] == "cmd"
    assert "timeout /t 2" in cmd[-1]
    assert "&&" in cmd[-1]
    assert r"C:\py.exe" in cmd[-1]


def test_start_script_command_windows(monkeypatch):
    monkeypatch.setattr(live_update.os, "name", "nt")
    cmd = live_update.start_script_command(r"C:\app\start.bat")
    assert cmd[0] == "cmd"
    assert "call" in cmd[-1]
    assert r"C:\app\start.bat" in cmd[-1]


def test_relaunch_returns_to_start_when_launched_from_it(monkeypatch):
    exits = []

    def fake_exit(code):
        exits.append(code)
        raise SystemExit(code)

    monkeypatch.setenv("MBD_LAUNCHER", "1")
    monkeypatch.setattr(live_update.time, "sleep", lambda _s: None)
    monkeypatch.setattr(live_update.os, "_exit", fake_exit)
    try:
        live_update._relaunch()
    except SystemExit:
        pass
    assert exits == [live_update.RESTART_EXIT]


def test_start_scripts_loop_on_restart_exit():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    bat = (root / "start.bat").read_text(encoding="utf-8")
    sh = (root / "start.sh").read_text(encoding="utf-8")
    assert "MBD_LAUNCHER=1" in bat
    assert "EQU 42" in bat
    assert "MBD_LAUNCHER=1" in sh
    assert "42" in sh
    assert "exec .venv/bin/python run.py" not in sh


def test_best_target_keeps_newer_popup_version():
    assert live_update._best_target("1.4.13", "1.4.14", "1.4.13", None) == "1.4.14"
    assert live_update._best_target("1.4.14", "1.4.13", "", None) == "1.4.14"


def test_apply_refuses_git_checkout(tmp_path, monkeypatch):
    monkeypatch.setenv("MBD_ROOT", str(tmp_path))
    result = live_update.apply_and_relaunch()
    assert result["ok"] is False
