"""Allow this app through Windows Firewall (in and out, private network)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _netsh(args: list[str]) -> bool:
    result = subprocess.run(
        ["netsh", "advfirewall", "firewall", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _add_port(name: str, direction: str, protocol: str, port: int) -> bool:
    _netsh(["delete", "rule", f"name={name}"])
    return _netsh(
        [
            "add",
            "rule",
            f"name={name}",
            f"dir={direction}",
            "action=allow",
            f"protocol={protocol}",
            f"localport={port}" if direction == "in" else f"remoteport={port}",
            "profile=private,domain",
            "enable=yes",
        ]
    )


def _add_program(name: str, direction: str, exe: str) -> bool:
    if not exe or not Path(exe).is_file():
        return True
    _netsh(["delete", "rule", f"name={name}"])
    return _netsh(
        [
            "add",
            "rule",
            f"name={name}",
            f"dir={direction}",
            "action=allow",
            f"program={exe}",
            "profile=private,domain",
            "enable=yes",
        ]
    )


def allow_port(port: int, python_exe: str | None = None) -> bool:
    if sys.platform != "win32":
        return False
    port = int(port)
    exe = python_exe or sys.executable
    ok = True
    for protocol in ("TCP", "UDP"):
        ok = _add_port(f"MBD-{protocol}-{port}-in", "in", protocol, port) and ok
        if protocol == "TCP":
            ok = _add_port(f"MBD-{protocol}-{port}-out", "out", protocol, port) and ok
    stem = Path(exe).stem
    ok = _add_program(f"MBD-{stem}-in", "in", exe) and ok
    ok = _add_program(f"MBD-{stem}-out", "out", exe) and ok
    return ok
