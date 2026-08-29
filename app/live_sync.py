"""Poll paired own-devices and pull when only the peer changed."""

from __future__ import annotations

import json
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sqlalchemy.orm import Session

from app.settings import get as setting
from app.snapshot import apply_snapshot, dump_snapshot, snapshot_counts, would_shrink
from app.sync import (
    compare_fingerprints,
    load_state,
    save_state,
    set_conflict,
    set_last_agreed,
    upsert_peer,
)

POLL_EVERY_SEC = 30
REQUEST_TIMEOUT = 8
LAN_TIMEOUT = 2.5

_lock = threading.Lock()
_wakeup = threading.Event()
_started = False
_last_error: str | None = None


def _headers(token: str | None = None) -> dict:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def peer_hosts(peer: dict) -> list[str]:
    hosts: list[str] = []
    lan = peer.get("lan_host") or ""
    wan = peer.get("wan_host") or ""
    port = int(peer.get("port") or 5050)
    fallback = peer.get("host") or ""
    if lan:
        hosts.append(lan if ":" in lan else f"{lan}:{port}")
    if fallback and fallback not in hosts:
        hosts.append(fallback if ":" in str(fallback) else f"{fallback}:{port}")
    if wan:
        item = wan if ":" in wan else f"{wan}:{port}"
        if item not in hosts:
            hosts.append(item)
    return hosts


def fetch_json(hosts: list[str], path: str, token: str | None = None, timeout: float = REQUEST_TIMEOUT) -> dict:
    last_error: Exception | None = None
    for index, host in enumerate(hosts):
        wait = LAN_TIMEOUT if index == 0 and len(hosts) > 1 else timeout
        req = Request(f"http://{host}{path}", headers=_headers(token))
        try:
            with urlopen(req, timeout=wait) as resp:
                payload = json.load(resp)
            if not isinstance(payload, dict):
                raise ValueError("The other computer sent something that is not a log.")
            return payload
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    raise URLError("No address to try.")


def post_json(hosts: list[str], path: str, body: dict, token: str | None = None) -> dict:
    raw = json.dumps(body).encode("utf-8")
    last_error: Exception | None = None
    for index, host in enumerate(hosts):
        wait = LAN_TIMEOUT if index == 0 and len(hosts) > 1 else REQUEST_TIMEOUT
        req = Request(
            f"http://{host}{path}",
            data=raw,
            headers={**_headers(token), "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=wait) as resp:
                if resp.length == 0:
                    return {}
                payload = json.load(resp)
            return payload if isinstance(payload, dict) else {}
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    return {}


def _session() -> Session:
    from app.db import SessionLocal

    if SessionLocal is None:
        raise RuntimeError("Database is not initialised.")
    return SessionLocal()


def freshness(session: Session | None = None) -> dict:
    """What a save should do: ok / pull / conflict / wait."""
    own = False
    close = False
    if session is None:
        session = _session()
        close = True
        own = True
    try:
        local = dump_snapshot(session)
        state = load_state()
        last = state.get("last_agreed") or ""
        peers = state.get("peers") or []
        if not peers or not setting("auto_sync"):
            return {
                "action": "ok",
                "needs_confirm": False,
                "local": local["counts"],
                "fingerprint": local["fingerprint"],
            }
        best = None
        for peer in peers:
            try:
                remote = fetch_json(peer_hosts(peer), "/api/sync/status", peer.get("token"))
            except Exception:  # noqa: BLE001
                continue
            action = compare_fingerprints(local["fingerprint"], remote.get("fingerprint") or "", last)
            info = {
                "action": action,
                "peer": peer,
                "remote": remote,
                "local": local["counts"],
                "shrink": would_shrink(local["counts"], remote.get("counts") or {}),
            }
            if action in ("pull", "conflict"):
                best = info
                break
            best = info
        if best is None:
            return {
                "action": "offline",
                "needs_confirm": False,
                "local": local["counts"],
                "fingerprint": local["fingerprint"],
            }
        needs = best["action"] in ("pull", "conflict") or best.get("shrink")
        return {
            "action": best["action"],
            "needs_confirm": bool(needs),
            "peer_name": (best["peer"] or {}).get("nickname") or "the other computer",
            "peer_id": (best["peer"] or {}).get("id"),
            "local": local["counts"],
            "remote": (best["remote"] or {}).get("counts"),
            "shrink": best.get("shrink"),
            "fingerprint": local["fingerprint"],
        }
    finally:
        if close and own:
            session.close()


def pull_peer(session: Session, peer: dict, *, force: bool = False) -> dict:
    remote = fetch_json(peer_hosts(peer), "/api/sync/snapshot", peer.get("token"))
    snap = remote.get("snapshot") if isinstance(remote.get("snapshot"), dict) else remote
    if "accounts" not in snap:
        raise ValueError("The other computer did not send a log.")
    local = dump_snapshot(session)
    if would_shrink(local, snap) and not force:
        set_conflict(
            {
                "peer_id": peer.get("id"),
                "peer_name": peer.get("nickname") or "the other computer",
                "local": local["counts"],
                "remote": snapshot_counts(snap),
                "shrink": True,
                "reason": "smaller",
            }
        )
        raise ValueError(
            f"Replace {local['counts']['bets']} bets with {snapshot_counts(snap)['bets']} "
            f"from {peer.get('nickname') or 'the other computer'}? Confirm to overwrite."
        )
    counts = apply_snapshot(session, snap, backup_why="before-sync")
    set_last_agreed(snap.get("fingerprint") or dump_snapshot(session)["fingerprint"])
    upsert_peer(
        {
            **peer,
            "last_seen": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "online": True,
        }
    )
    return counts


def _poll_once() -> None:
    global _last_error
    if not setting("auto_sync") or not setting("allow_lan"):
        return
    state = load_state()
    peers = state.get("peers") or []
    if not peers:
        return
    session = _session()
    try:
        local = dump_snapshot(session)
        last = state.get("last_agreed") or ""
        for peer in peers:
            try:
                remote = fetch_json(peer_hosts(peer), "/api/sync/status", peer.get("token"))
            except Exception as exc:  # noqa: BLE001
                _last_error = str(exc)
                upsert_peer({**peer, "online": False})
                continue
            _last_error = None
            upsert_peer({**peer, "online": True, "last_seen": time.strftime("%Y-%m-%dT%H:%M:%S")})
            action = compare_fingerprints(local["fingerprint"], remote.get("fingerprint") or "", last)
            if action == "same":
                if last != local["fingerprint"]:
                    set_last_agreed(local["fingerprint"])
                continue
            if action == "wait":
                continue
            if action == "conflict":
                set_conflict(
                    {
                        "peer_id": peer.get("id"),
                        "peer_name": peer.get("nickname") or "the other computer",
                        "local": local["counts"],
                        "remote": remote.get("counts"),
                        "shrink": would_shrink(local["counts"], remote.get("counts") or {}),
                        "reason": "both",
                    }
                )
                continue
            if action == "pull":
                if would_shrink(local["counts"], remote.get("counts") or {}):
                    set_conflict(
                        {
                            "peer_id": peer.get("id"),
                            "peer_name": peer.get("nickname") or "the other computer",
                            "local": local["counts"],
                            "remote": remote.get("counts"),
                            "shrink": True,
                            "reason": "smaller",
                        }
                    )
                    continue
                pull_peer(session, peer)
                session.commit()
                from app.settings import get as setting_get
                from app.excel import sync_workbook

                if setting_get("excel_sync"):
                    try:
                        sync_workbook(session)
                    except Exception:  # noqa: BLE001
                        pass
                local = dump_snapshot(session)
    finally:
        session.close()


def tick() -> None:
    if not _lock.acquire(blocking=False):
        return
    try:
        _poll_once()
    finally:
        _lock.release()


def notify_after_save() -> None:
    _wakeup.set()


def last_error() -> str | None:
    return _last_error


def _loop() -> None:
    while True:
        _wakeup.wait(timeout=POLL_EVERY_SEC)
        _wakeup.clear()
        try:
            tick()
        except Exception:  # noqa: BLE001
            pass


def start_background() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="mbd-live-sync", daemon=True).start()
