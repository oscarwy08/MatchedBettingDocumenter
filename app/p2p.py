"""Find paired computers on the same Wi‑Fi. Own UDP only — no third-party relay."""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import Callable

from app.nat import is_cgnat, is_lan_ip, lan_ip, local_ipv4s

ANNOUNCE_EVERY_SEC = 8
_seen: dict[str, dict] = {}
_seen_lock = threading.Lock()
_started = False
_port = 5050
_on_peer: Callable[[dict], None] | None = None


def punch_port(http_port: int) -> int:
    return int(http_port)


def remember(device_id: str, host: str, *, nickname: str = "") -> None:
    if not device_id or not host:
        return
    with _seen_lock:
        _seen[device_id] = {
            "device_id": device_id,
            "host": host,
            "nickname": nickname,
            "seen_at": time.time(),
        }


def host_for(device_id: str | None) -> str | None:
    if not device_id:
        return None
    with _seen_lock:
        item = _seen.get(device_id)
    if not item:
        return None
    if time.time() - float(item.get("seen_at") or 0) > 90:
        return None
    return item.get("host") or None


def known() -> list[dict]:
    now = time.time()
    with _seen_lock:
        return [dict(item) for item in _seen.values() if now - float(item.get("seen_at") or 0) <= 90]


def announce_payload(http_port: int) -> dict:
    from app.sync import ensure_state

    state = ensure_state()
    ip = lan_ip()
    return {
        "v": 1,
        "t": "here",
        "device_id": state["device_id"],
        "nickname": state.get("nickname") or "",
        "http": f"{ip}:{http_port}",
        "port": http_port,
    }


def apply_announce(payload: dict) -> dict | None:
    """If this is a paired computer, remember its current address."""
    if not isinstance(payload, dict) or payload.get("t") != "here":
        return None
    device_id = str(payload.get("device_id") or "")
    host = str(payload.get("http") or "")
    if not device_id or not host:
        return None
    from app.sync import load_state

    me = load_state()
    if device_id == me.get("device_id"):
        return None
    if not any(peer.get("device_id") == device_id for peer in me.get("peers") or []):
        return None
    remember(device_id, host, nickname=str(payload.get("nickname") or ""))
    if _on_peer is not None:
        _on_peer({"device_id": device_id, "host": host, "nickname": payload.get("nickname")})
    return {"device_id": device_id, "host": host}


def _broadcast_addrs() -> list[str]:
    addrs = {"255.255.255.255", "<broadcast>"}
    for ip in local_ipv4s():
        if not is_lan_ip(ip) or is_cgnat(ip):
            continue
        parts = ip.split(".")
        if len(parts) == 4:
            addrs.add(".".join(parts[:3] + ["255"]))
    return [item for item in addrs if item != "<broadcast>"] + (["<broadcast>"] if "<broadcast>" in addrs else [])


def _send_announce(port: int) -> None:
    payload = json.dumps(announce_payload(port), separators=(",", ":")).encode("utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        for dest in _broadcast_addrs():
            try:
                sock.sendto(payload, (dest, port))
            except OSError:
                continue
    finally:
        sock.close()


def _listen_loop(port: int) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("0.0.0.0", port))
        sock.settimeout(1.0)
        while True:
            try:
                data, _addr = sock.recvfrom(4096)
            except TimeoutError:
                continue
            except OSError:
                break
            try:
                payload = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            apply_announce(payload)
    finally:
        sock.close()


def _announce_loop(port: int) -> None:
    while True:
        try:
            _send_announce(port)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(ANNOUNCE_EVERY_SEC)


def start_background(http_port: int) -> None:
    global _started, _port
    if _started:
        return
    _started = True
    _port = punch_port(http_port)
    threading.Thread(target=_listen_loop, args=(_port,), name="mbd-p2p-listen", daemon=True).start()
    threading.Thread(target=_announce_loop, args=(_port,), name="mbd-p2p-announce", daemon=True).start()
