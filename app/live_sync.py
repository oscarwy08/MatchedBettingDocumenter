"""HTTP helpers for pairing, Friends LAN, and the Wi‑Fi half of replicate."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from sqlalchemy.orm import Session

from app.snapshot import apply_snapshot, dump_snapshot, snapshot_counts, would_shrink
from app.sync import (
    load_state,
    set_conflict,
    set_last_agreed,
    upsert_peer,
)

REQUEST_TIMEOUT = 3.0
LAN_TIMEOUT = 2.0


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
    # Last address that actually answered first — one-way streets stay on the working lane.
    for raw in (
        peer.get("last_ok_host"),
        discovered,
        peer.get("lan_host"),
        peer.get("host"),
        peer.get("wan_host"),
    ):
        item = _host_item(raw, port)
        if not item or item in hosts:
            continue
        ip = item.rsplit(":", 1)[0].strip("[]")
        if is_cgnat(ip):
            continue
        hosts.append(item)
    return hosts


def _mark_ok(peer: dict, host: str) -> None:
    if not host:
        return
    peer["last_ok_host"] = host
    peer["can_reach"] = True
    peer["online"] = True
    peer["last_via"] = "wifi"
    peer["last_seen"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    upsert_peer(peer)


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


def fetch_json_host(
    hosts: list[str], path: str, token: str | None = None, timeout: float = REQUEST_TIMEOUT
) -> tuple[dict, str]:
    unique = []
    for host in hosts:
        if host and host not in unique:
            unique.append(host)
    if not unique:
        raise URLError("No address to try.")
    errors: list[str] = []
    if len(unique) == 1:
        try:
            return _fetch_one(unique[0], path, token, timeout), unique[0]
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
                    return future.result(), host
                except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
                    errors.append(f"{host}: {exc}")
        except TimeoutError:
            errors.append("timed out")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    raise URLError("; ".join(errors) if errors else "No address to try.")


def fetch_json(hosts: list[str], path: str, token: str | None = None, timeout: float = REQUEST_TIMEOUT) -> dict:
    payload, _host = fetch_json_host(hosts, path, token, timeout)
    return payload


def post_json_host(hosts: list[str], path: str, body: dict, token: str | None = None) -> tuple[dict, str]:
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
                    return {}, host
                payload = json.load(resp)
            return (payload if isinstance(payload, dict) else {}), host
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    return {}, ""


def post_json(hosts: list[str], path: str, body: dict, token: str | None = None) -> dict:
    payload, _host = post_json_host(hosts, path, body, token)
    return payload


def _tokens_for(peer: dict) -> list[str]:
    # Our token first — that is what a successful send already uses.
    mine = load_state().get("device_token")
    tokens: list[str] = []
    for token in (mine, peer.get("token")):
        text = str(token or "").strip()
        if text and text not in tokens:
            tokens.append(text)
    return tokens


def fetch_peer(peer: dict, path: str, timeout: float = REQUEST_TIMEOUT) -> dict:
    last_error: Exception | None = None
    for token in _tokens_for(peer):
        try:
            payload, host = fetch_json_host(peer_hosts(peer), path, token, timeout)
            _mark_ok(peer, host)
            return payload
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    raise URLError("No address to try.")


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
    """Local only — never dial the other computer from a save."""
    migrate_last_agreed(session)
    local = dump_snapshot(session)
    state = load_state()
    conflict = state.get("conflict")
    if conflict:
        return {
            "action": "conflict",
            "needs_confirm": False,
            "peer_name": conflict.get("peer_name") or "the other computer",
            "peer_id": conflict.get("peer_id"),
            "local": conflict.get("local") or local["counts"],
            "remote": conflict.get("remote"),
            "shrink": conflict.get("shrink"),
            "fingerprint": local["fingerprint"],
        }
    return {
        "action": "ok",
        "needs_confirm": False,
        "local": local["counts"],
        "fingerprint": local["fingerprint"],
    }


def pull_peer(session: Session, peer: dict, *, force: bool = False) -> dict:
    from app.replicate import pull_from_anywhere

    return pull_from_anywhere(session, peer, force=force)


def apply_push(session: Session, body: dict, *, force: bool = False) -> dict:
    snap = body.get("snapshot") if isinstance(body.get("snapshot"), dict) else body
    if not isinstance(snap, dict) or "accounts" not in snap:
        raise ValueError("The other computer did not send a log.")
    local = dump_snapshot(session)
    incoming_fp = snap.get("fingerprint") or ""
    if incoming_fp and incoming_fp == local["fingerprint"]:
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
        existing = None
        for peer in load_state().get("peers") or []:
            if peer.get("device_id") == body.get("device_id"):
                existing = peer
                break
        if existing:
            _refresh_peer_address(existing, body)
            existing["last_via"] = "wifi"
            upsert_peer(existing)
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
        _payload, host = post_json_host(
            hosts, "/api/sync/push", _push_body(session), load_state().get("device_token")
        )
        _mark_ok(peer, host)
        return True
    except Exception:  # noqa: BLE001
        return False


def notify_after_save() -> None:
    from app.replicate import notify_after_save as replicate_notify

    replicate_notify()


def last_error() -> str | None:
    from app.replicate import last_error as replicate_error

    return replicate_error()


def start_background() -> None:
    from app.replicate import start_background as start_replicate

    start_replicate()
