"""LAN pairing codes: 482193@192.168.1.10:5050"""

from __future__ import annotations

import random
import socket

_share_pin: str | None = None


def lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def make_link_code(pin: str, port: int = 5050) -> str:
    return f"{pin}@{lan_ip()}:{port}"


def parse_link_code(raw: str) -> tuple[str, str]:
    text = (raw or "").strip().replace(" ", "")
    if "@" not in text:
        raise ValueError("Link code looks like 482193@192.168.1.10:5050")
    pin, host = text.split("@", 1)
    pin = pin.strip()
    host = host.strip()
    if not pin.isdigit() or len(pin) != 6:
        raise ValueError("The PIN at the start of the code should be 6 digits.")
    if not host:
        raise ValueError("The code is missing the other computer's address.")
    if ":" not in host:
        host = f"{host}:5050"
    return pin, host


def start_share() -> str:
    global _share_pin
    _share_pin = f"{random.randint(0, 999999):06d}"
    return _share_pin


def stop_share() -> None:
    global _share_pin
    _share_pin = None


def current_pin() -> str | None:
    return _share_pin
