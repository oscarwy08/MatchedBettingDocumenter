"""Allow this app through a Linux firewall (ufw, firewalld, or iptables)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def allow_port(port: int) -> bool:
    if not sys.platform.startswith("linux"):
        return False
    port = int(port)
    if _ufw_allows(port):
        return True
    if _run_ufw(port) or _run_firewalld(port) or _run_iptables(port):
        return True
    return _ufw_allows(port)


def _ufw_allows(port: int) -> bool:
    path = Path("/etc/ufw/user.rules")
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    needle = f"--dport {port} "
    return needle in text and " -j ACCEPT" in text


def _run_ufw(port: int) -> bool:
    exe = shutil.which("ufw")
    if not exe:
        return False
    ok = True
    for proto in ("tcp", "udp"):
        result = subprocess.run(
            [exe, "allow", f"{port}/{proto}", "comment", "Matched Betting Documenter"],
            check=False,
            capture_output=True,
            text=True,
        )
        ok = result.returncode == 0 and ok
    return ok


def _run_firewalld(port: int) -> bool:
    exe = shutil.which("firewall-cmd")
    if not exe:
        return False
    added = subprocess.run(
        [exe, "--permanent", f"--add-port={port}/tcp"],
        check=False,
        capture_output=True,
        text=True,
    )
    if added.returncode != 0:
        return False
    subprocess.run([exe, "--reload"], check=False, capture_output=True, text=True)
    return True


def _run_iptables(port: int) -> bool:
    exe = shutil.which("iptables")
    if not exe:
        return False
    check = subprocess.run(
        [exe, "-C", "INPUT", "-p", "tcp", "--dport", str(port), "-j", "ACCEPT"],
        check=False,
        capture_output=True,
        text=True,
    )
    if check.returncode == 0:
        return True
    added = subprocess.run(
        [exe, "-I", "INPUT", "-p", "tcp", "--dport", str(port), "-j", "ACCEPT"],
        check=False,
        capture_output=True,
        text=True,
    )
    return added.returncode == 0
