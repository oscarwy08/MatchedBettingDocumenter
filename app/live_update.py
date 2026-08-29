"""In-app update status. Reads a tiny latest.txt, not the GitHub API."""

from __future__ import annotations

import os
import sys
import threading
import time

CHECK_EVERY_SEC = 60
RESTART_EXIT = 42

_lock = threading.Lock()
_checked_at = 0.0
_latest = ""
_error = ""


def status(*, refresh: bool = False) -> dict:
    from app.version import VERSION

    import update
    from app.settings import get as setting

    if not setting("update_popup"):
        return {
            "current": VERSION,
            "latest": VERSION,
            "available": False,
            "dev": False,
            "disabled": True,
        }
    if update._is_dev_checkout():
        return {
            "current": VERSION,
            "latest": VERSION,
            "available": False,
            "dev": True,
        }
    _maybe_refresh(force=refresh)
    latest = _latest
    return {
        "current": VERSION,
        "latest": latest or VERSION,
        "available": bool(latest) and update.is_newer(latest, VERSION),
        "dev": False,
        "error": _error,
    }


def apply_and_relaunch(*, requested: str | None = None) -> dict:
    import tempfile
    from pathlib import Path

    import update
    from app.version import VERSION

    from app.settings import get as setting

    if not setting("update_popup"):
        return {"ok": False, "error": "The update popup is turned off in Settings."}
    if update._is_dev_checkout():
        return {"ok": False, "error": "This git checkout does not auto-update."}
    repo = update.configured_repo()
    if not repo:
        return {"ok": False, "error": "No update repo configured."}
    cached = _latest
    peek = status(refresh=True)
    target = _best_target(VERSION, cached, peek.get("latest"), requested)
    if not update.is_newer(target, VERSION):
        return {"ok": False, "error": "Already up to date."}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / update.ASSET_NAME
            update._http_download(update.latest_zip_url(repo), zip_path)
            update.apply_zip(zip_path)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    threading.Thread(target=_relaunch, daemon=True).start()
    return {"ok": True, "restarting": True}


def _maybe_refresh(*, force: bool = False) -> None:
    global _checked_at, _latest, _error
    now = time.monotonic()
    with _lock:
        if not force and _checked_at and now - _checked_at < CHECK_EVERY_SEC:
            return
        import update

        repo = update.configured_repo()
        if not repo:
            _checked_at = now
            _error = ""
            return
        try:
            found = update.fetch_published_version(repo) or ""
            _latest = found
            _error = ""
        except Exception as exc:  # noqa: BLE001
            _error = str(exc)
        _checked_at = now


def _best_target(current: str, *candidates: str | None) -> str:
    import update

    best = current
    for raw in candidates:
        text = (raw or "").strip()
        if text and update.is_newer(text, best):
            best = text
    return best


def restart_command(python: str, run_py: str) -> list[str]:
    """Start the new copy after this process has exited and dropped the port."""
    import shlex

    if os.name == "nt":
        return [
            "cmd",
            "/c",
            f'timeout /t 2 /nobreak >nul && "{python}" "{run_py}"',
        ]
    return ["/bin/sh", "-c", f"sleep 1; exec {shlex.quote(python)} {shlex.quote(run_py)}"]


def start_script_command(start_script: str) -> list[str]:
    """Relaunch via Start so the window stays open (pause / loop)."""
    if os.name == "nt":
        return [
            "cmd",
            "/c",
            f'timeout /t 2 /nobreak >nul && call "{start_script}"',
        ]
    import shlex

    return ["/bin/sh", "-c", f"sleep 1; exec {shlex.quote(start_script)}"]


def _start_script():
    import update

    front = update.install_root()
    name = "start.bat" if os.name == "nt" else "start.sh"
    path = front / name
    return path if path.is_file() else None


def _relaunch() -> None:
    import subprocess

    import update

    time.sleep(0.8)
    if os.environ.get("MBD_LAUNCHER") == "1":
        os._exit(RESTART_EXIT)

    start_script = _start_script()
    run_py = update._package_dir() / "run.py"
    kwargs = {
        "env": os.environ.copy(),
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        )
    else:
        kwargs["start_new_session"] = True
    if start_script is not None:
        kwargs["cwd"] = str(start_script.parent)
        subprocess.Popen(start_script_command(str(start_script)), **kwargs)
        os._exit(0)
    if not run_py.is_file():
        return
    kwargs["cwd"] = str(run_py.parent)
    subprocess.Popen(restart_command(sys.executable, str(run_py)), **kwargs)
    os._exit(0)
