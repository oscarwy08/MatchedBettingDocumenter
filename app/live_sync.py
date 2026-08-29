"""Poll paired own-devices and pull when only the peer changed."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from sqlalchemy.orm import Session

from app.settings import get as setting
from app.snapshot import apply_snapshot, dump_snapshot, snapshot_counts, would_shrink
from app.sync import (
    clear_want_push_for,
    compare_fingerprints,
    load_state,
    set_conflict,
    set_last_agreed,
    set_want_push,
    upsert_peer,
)

POLL_EVERY_SEC = 8
REQUEST_TIMEOUT = 3.0
LAN_TIMEOUT = 2.0
FRESHNESS_TIMEOUT = 1.5

_lock = threading.Lock()
_wakeup = threading.Event()
_started = False
_last_error: str | None = None


def _headers(token: str | None = None) -> dict:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        from app.nat import lan_ip
        from app.settings import get as setting_get
        from app.sync import ensure_state

        me = ensure_state()
        headers["X-MBD-Device-Id"] = str(me.get("device_id") or "")
        headers["X-MBD-Device-Token"] = str(me.get("device_token") or "")
        headers["X-MBD-Nickname"] = str(me.get("nickname") or "")
        headers["X-MBD-Lan"] = lan_ip()
        headers["X-MBD-Port"] = str(int(setting_get("port")))
    except Exception:  # noqa: BLE001
        pass
    return headers


def _host_item(raw, port: int) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    if ":" in text:
        return text
    return f"{text}:{port}"


def peer_hosts(peer: dict) -> list[str]:
    from app.nat import is_cgnat
    from app.p2p import host_for

    hosts: list[str] = []
    port = int(peer.get("port") or 5050)
    discovered = host_for(peer.get("device_id"))
    for raw in (discovered, peer.get("lan_host"), peer.get("host"), peer.get("wan_host")):
        item = _host_item(raw, port)
        if not item or item in hosts:
            continue
        ip = item.rsplit(":", 1)[0].strip("[]")
        if is_cgnat(ip):
            continue
        hosts.append(item)
    return hosts


def _fetch_one(host: str, path: str, token: str | None, timeout: float) -> dict:
    url = f"http://{host}{path}"
    if token:
        joiner = "&" if "?" in path else "?"
        url = f"{url}{joiner}token={quote(token, safe='')}"
    req = Request(url, headers=_headers(token))
    with urlopen(req, timeout=timeout) as resp:
        payload = json.load(resp)
    if not isinstance(payload, dict):
        raise ValueError("The other computer sent something that is not a log.")
    return payload


def _refresh_peer_address(peer: dict, remote: dict) -> None:
    lan = remote.get("lan_ip") or ""
    ips = [ip for ip in (remote.get("lan_ips") or []) if ip]
    if lan and lan not in ips:
        ips.insert(0, lan)
    if not ips and not lan:
        return
    port = int(remote.get("port") or peer.get("port") or 5050)
    host = f"{(lan or ips[0])}:{port}"
    from app.p2p import remember

    if peer.get("device_id") and host:
        remember(str(peer["device_id"]), host, nickname=str(remote.get("nickname") or ""))
    upsert_peer(
        {
            **peer,
            "lan_host": lan or peer.get("lan_host") or "",
            "host": host or peer.get("host") or "",
            "port": port,
            "last_seen": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "online": True,
        }
    )


def migrate_last_agreed(session: Session) -> None:
    from app.snapshot import fingerprint_bets_only

    local = dump_snapshot(session)
    last = load_state().get("last_agreed") or ""
    if last and last == fingerprint_bets_only(local) and last != local["fingerprint"]:
        set_last_agreed(local["fingerprint"])


def fetch_json(hosts: list[str], path: str, token: str | None = None, timeout: float = REQUEST_TIMEOUT) -> dict:
    unique = []
    for host in hosts:
        if host and host not in unique:
            unique.append(host)
    if not unique:
        raise URLError("No address to try.")
    errors: list[str] = []
    if len(unique) == 1:
        try:
            return _fetch_one(unique[0], path, token, timeout)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise URLError(f"{unique[0]}: {exc}") from exc
    pool = ThreadPoolExecutor(max_workers=len(unique))
    try:
        futures = {pool.submit(_fetch_one, host, path, token, timeout): host for host in unique}
        try:
            done = as_completed(futures, timeout=timeout + 0.4)
            for future in done:
                host = futures[future]
                try:
                    return future.result()
                except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
                    errors.append(f"{host}: {exc}")
        except TimeoutError:
            errors.append("timed out")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    raise URLError("; ".join(errors) if errors else "No address to try.")


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
        return _freshness(session)
    except Exception:  # noqa: BLE001
        return {"action": "ok", "needs_confirm": False, "local": {}, "fingerprint": ""}
    finally:
        if close and own:
            session.close()


def _freshness(session: Session) -> dict:
    migrate_last_agreed(session)
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
            remote = fetch_json(
                peer_hosts(peer),
                "/api/sync/status",
                peer.get("token"),
                timeout=FRESHNESS_TIMEOUT,
            )
        except Exception:  # noqa: BLE001
            continue
        _refresh_peer_address(peer, remote)
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


def pull_peer(session: Session, peer: dict, *, force: bool = False) -> dict:
    try:
        remote = fetch_json(peer_hosts(peer), "/api/sync/snapshot", peer.get("token"))
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        if peer.get("id"):
            set_want_push(str(peer["id"]), True)
        raise ValueError(
            "This Windows PC cannot call the other computer (incoming connections are often blocked). "
            "Asked them to send the log instead — leave both apps open, then refresh Devices in a few seconds."
        ) from exc
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


def apply_push(session: Session, body: dict, *, force: bool = False) -> dict:
    snap = body.get("snapshot") if isinstance(body.get("snapshot"), dict) else body
    if not isinstance(snap, dict) or "accounts" not in snap:
        raise ValueError("The other computer did not send a log.")
    local = dump_snapshot(session)
    incoming_fp = snap.get("fingerprint") or ""
    if incoming_fp and incoming_fp == local["fingerprint"]:
        if body.get("device_id"):
            clear_want_push_for(str(body["device_id"]))
        return {"same": True, **local["counts"]}
    last = load_state().get("last_agreed") or ""
    we_unchanged = bool(last and last == local["fingerprint"])
    if would_shrink(local, snap) and not force and not we_unchanged:
        set_conflict(
            {
                "peer_id": body.get("device_id"),
                "peer_name": body.get("nickname") or "the other computer",
                "local": local["counts"],
                "remote": snapshot_counts(snap),
                "shrink": True,
                "reason": "smaller",
            }
        )
        raise ValueError(
            f"Replace {local['counts']['bets']} bets with {snapshot_counts(snap)['bets']} "
            f"from {body.get('nickname') or 'the other computer'}? Confirm to overwrite."
        )
    counts = apply_snapshot(session, snap, backup_why="before-sync")
    set_last_agreed(snap.get("fingerprint") or dump_snapshot(session)["fingerprint"])
    if body.get("device_id"):
        clear_want_push_for(str(body["device_id"]))
        existing = None
        for peer in load_state().get("peers") or []:
            if peer.get("device_id") == body.get("device_id"):
                existing = peer
                break
        if existing:
            _refresh_peer_address(existing, body)
    return {"same": False, **counts}


def _push_body(session: Session) -> dict:
    from app.nat import lan_ip
    from app.settings import get as setting_get

    me = load_state()
    return {
        "snapshot": dump_snapshot(session),
        "device_id": me["device_id"],
        "nickname": me.get("nickname") or "",
        "lan_ip": lan_ip(),
        "lan_ips": [],
        "port": int(setting_get("port")),
    }


def push_one(session: Session, peer: dict) -> bool:
    hosts = peer_hosts(peer)
    if not hosts:
        return False
    try:
        post_json(hosts, "/api/sync/push", _push_body(session), load_state().get("device_token"))
        return True
    except Exception:  # noqa: BLE001
        return False


def push_to_peers(session: Session | None = None) -> None:
    if not setting("auto_sync") or not setting("allow_lan"):
        return
    peers = load_state().get("peers") or []
    if not peers:
        return
    own = session is None
    if own:
        session = _session()
    try:
        migrate_last_agreed(session)
        for peer in peers:
            push_one(session, peer)
    finally:
        if own and session is not None:
            session.close()


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
        migrate_last_agreed(session)
        local = dump_snapshot(session)
        last = state.get("last_agreed") or ""
        for peer in peers:
            try:
                remote = fetch_json(peer_hosts(peer), "/api/sync/status", peer.get("token"), timeout=LAN_TIMEOUT)
            except Exception as exc:  # noqa: BLE001
                _last_error = str(exc)
                upsert_peer({**peer, "online": False})
                continue
            _last_error = None
            _refresh_peer_address(peer, remote)
            upsert_peer({**peer, "online": True, "last_seen": time.strftime("%Y-%m-%dT%H:%M:%S")})
            me_id = load_state().get("device_id")
            if me_id and me_id in (remote.get("want_push_from") or []):
                push_one(session, peer)
            action = compare_fingerprints(local["fingerprint"], remote.get("fingerprint") or "", last)
            if action == "same":
                if last != local["fingerprint"]:
                    set_last_agreed(local["fingerprint"])
                continue
            if action == "wait":
                # We changed; they cannot always dial us (Windows). Send the log.
                push_one(session, peer)
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
                # Only they changed — including a delete — so take their log.
                pull_peer(session, peer, force=True)
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
    threading.Thread(target=_push_after_save, name="mbd-push", daemon=True).start()


def _push_after_save() -> None:
    if not _lock.acquire(blocking=False):
        return
    try:
        push_to_peers()
    except Exception:  # noqa: BLE001
        pass
    finally:
        _lock.release()


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
