"""Allow this app's listen port through Windows Firewall (private networks)."""

from __future__ import annotations

import subprocess
import sys


def allow_port(port: int) -> bool:
    if sys.platform != "win32":
        return False
    name = "MatchedBettingDocumenter"
    port = int(port)
    for protocol in ("TCP", "UDP"):
        rule = f"{name}-{protocol}-{port}"
        subprocess.run(
            [
                "netsh",
                "advfirewall",
                "firewall",
                "delete",
                "rule",
                f"name={rule}",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        added = subprocess.run(
            [
                "netsh",
                "advfirewall",
                "firewall",
                "add",
                "rule",
                f"name={rule}",
                "dir=in",
                "action=allow",
                f"protocol={protocol}",
                f"localport={port}",
                "profile=private,domain",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if added.returncode != 0:
            return False
    return True
