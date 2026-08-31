"""Open this app's listen port on the local firewall. Elevated from Settings."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def marker_path() -> Path:
    from app.paths import data_dir

    return data_dir() / "firewall.ok"


def is_open(port: int) -> bool:
    port = int(port)
    if sys.platform.startswith("linux"):
        from app.linux_firewall import _ufw_allows

        if _ufw_allows(port):
            return True
    path = marker_path()
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8").strip()
    return text in {"ok", str(port)}


def apply(port: int) -> bool:
    port = int(port)
    if sys.platform == "win32":
        from app.win_firewall import allow_port

        ok = allow_port(port)
    elif sys.platform.startswith("linux"):
        from app.linux_firewall import allow_port

        ok = allow_port(port)
    else:
        ok = True
    if ok:
        _write_marker(port)
    return ok


def launch_elevated(port: int) -> bool:
    port = int(port)
    script = Path(__file__).resolve()
    work = script.parent.parent
    from app.paths import root_dir

    root = str(root_dir())
    python = sys.executable
    if sys.platform == "win32":
        inner = (
            f"Start-Process -FilePath {_ps(python)} -WorkingDirectory {_ps(str(work))} "
            f"-ArgumentList {_ps(str(script))},{_ps(str(port))},{_ps(root)} -Verb RunAs"
        )
        subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", inner],
            close_fds=True,
        )
        return True
    helper = shutil.which("pkexec")
    if not helper:
        return False
    subprocess.Popen(
        [helper, python, str(script), str(port), root],
        cwd=str(work),
        start_new_session=True,
        close_fds=True,
    )
    return True


def _write_marker(port: int) -> None:
    path = marker_path()
    path.write_text(f"{int(port)}\n", encoding="utf-8")


def _ps(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


if __name__ == "__main__":
    here = Path(__file__).resolve().parent.parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5050
    if len(sys.argv) > 2:
        os.environ["MBD_ROOT"] = sys.argv[2]
    raise SystemExit(0 if apply(port) else 1)
