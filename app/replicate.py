"""Keep paired computers equal: Wi‑Fi first, encrypted mailbox if that fails."""

from __future__ import annotations

import gzip
import json
import threading
import time

from sqlalchemy.orm import Session

from app.crypto import decrypt_bytes, encrypt_bytes
from app.settings import get as setting
from app.snapshot import apply_snapshot, dump_snapshot, snapshot_counts, would_shrink
from app.sync import compare_fingerprints, load_state, set_conflict, set_last_agreed, upsert_peer

POLL_EVERY_SEC = 4
KIND = "snap"
MAILBOX_TIMEOUT = 4.0

_lock = threading.Lock()
_wakeup = threading.Event()
_started = False
_last_error: str | None = None


def snap_key(pair_secret: str, device_id: str) -> str:
    return f"{pair_secret}:{device_id}"


def encode_snap(secret: str, payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
    return encrypt_bytes(secret, gzip.compress(raw))


def decode_snap(secret: str, blob: str) -> dict:
    packed = decrypt_bytes(secret, blob)
    try:
        raw = gzip.decompress(packed)
    except OSError:
        raw = packed
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("The mailbox did not send a log.")
    return payload


def _session() -> Session:
    from app.db import SessionLocal

    if SessionLocal is None:
        raise RuntimeError("Database is not initialised.")
    return SessionLocal()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _set_via(peer: dict, via: str, *, reachable: bool | None = None) -> None:
    peer["last_via"] = via
    peer["last_seen"] = _now()
    if via:
        peer["online"] = True
    if reachable is not None:
        peer["can_reach"] = reachable
    upsert_peer(peer)


def _envelope(session: Session) -> dict:
    me = load_state()
    snap = dump_snapshot(session)
    return {
        "device_id": me["device_id"],
        "nickname": me.get("nickname") or "",
        "fingerprint": snap["fingerprint"],
        "counts": snap["counts"],
        "snapshot": snap,
        "exported_at": snap.get("exported_at") or "",
    }


def publish_ours(session: Session) -> None:
    from app.mailbox import put

    me = load_state()
    secret = me.get("pair_secret") or ""
    device_id = me.get("device_id") or ""
    if not secret or not device_id:
        return
    blob = encode_snap(secret, _envelope(session))
    put(KIND, snap_key(secret, device_id), blob)
    # Same secret as Friends: one account topic so a stale device_id still finds the log.
    put(KIND, secret, blob)


def fetch_theirs(peer: dict | None = None) -> dict | None:
    from app.mailbox import get

    me = load_state()
    secret = me.get("pair_secret") or ""
    if not secret:
        return None
    keys = []
    peer_id = str((peer or {}).get("device_id") or "")
    if peer_id:
        keys.append(snap_key(secret, peer_id))
    keys.append(secret)
    seen = set()
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        blob = get(KIND, key, timeout=MAILBOX_TIMEOUT)
        if not blob:
            continue
        payload = decode_snap(secret, blob)
        if payload.get("device_id") == me.get("device_id"):
            continue
        return payload
    return None


def apply_remote_snap(session: Session, peer: dict, snap: dict, *, force: bool = False, via: str = "") -> dict:
    if not isinstance(snap, dict) or "accounts" not in snap:
        raise ValueError("The other computer did not send a log.")
    local = dump_snapshot(session)
    incoming_fp = snap.get("fingerprint") or ""
    if incoming_fp and incoming_fp == local["fingerprint"]:
        set_last_agreed(incoming_fp)
        if via:
            _set_via(peer, via)
        return {"same": True, **local["counts"]}
    last = load_state().get("last_agreed") or ""
    we_unchanged = bool(last and last == local["fingerprint"])
    if would_shrink(local, snap) and not force and not we_unchanged:
        set_conflict(
            {
                "peer_id": peer.get("id") or peer.get("device_id"),
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
    if via:
        _set_via(peer, via, reachable=via == "wifi")
    else:
        upsert_peer({**peer, "last_seen": _now(), "online": True})
    return {"same": False, **counts}


def _after_apply(session: Session) -> None:
    session.commit()
    if setting("excel_sync"):
        from app.excel import sync_workbook

        try:
            sync_workbook(session)
        except Exception:  # noqa: BLE001
            pass


def _decide(session: Session, peer: dict, local: dict, last: str, remote: dict, *, via: str) -> None:
    from app.live_sync import fetch_peer, push_one

    action = compare_fingerprints(local["fingerprint"], remote.get("fingerprint") or "", last)
    if action == "same":
        if last != local["fingerprint"]:
            set_last_agreed(local["fingerprint"])
        return
    if action == "wait":
        if via == "wifi":
            push_one(session, peer)
        return
    if action == "conflict":
        set_conflict(
            {
                "peer_id": peer.get("id"),
                "peer_name": peer.get("nickname") or "the other computer",
                "local": local["counts"],
                "remote": remote.get("counts") or {},
                "shrink": would_shrink(local["counts"], remote.get("counts") or {}),
                "reason": "both",
            }
        )
        return
    if action != "pull":
        return
    snap = remote.get("snapshot") if isinstance(remote.get("snapshot"), dict) else None
    if snap is None and via == "wifi":
        full = fetch_peer(peer, "/api/sync/snapshot")
        snap = full.get("snapshot") if isinstance(full.get("snapshot"), dict) else full
    if snap is None:
        return
    apply_remote_snap(session, peer, snap, force=True, via=via)
    _after_apply(session)


def try_lan(session: Session, peer: dict, local: dict, last: str) -> bool:
    from app.live_sync import fetch_peer, LAN_TIMEOUT, _refresh_peer_address

    if not setting("allow_lan"):
        return False
    try:
        remote = fetch_peer(peer, "/api/sync/status", timeout=LAN_TIMEOUT)
    except Exception:  # noqa: BLE001
        return False
    _refresh_peer_address(peer, remote)
    _set_via(peer, "wifi", reachable=True)
    _decide(session, peer, local, last, remote, via="wifi")
    return True


def try_mailbox(session: Session, peer: dict, local: dict, last: str) -> bool:
    global _last_error
    try:
        remote = fetch_theirs(peer)
    except Exception as exc:  # noqa: BLE001
        _last_error = str(exc)
        _set_via(peer, "mailbox", reachable=False)
        return False
    if remote is None:
        return True
    _last_error = None
    _set_via(peer, "mailbox", reachable=False)
    _decide(session, peer, local, last, remote, via="mailbox")
    return True


def pull_from_anywhere(session: Session, peer: dict, *, force: bool = False) -> dict:
    from app.live_sync import fetch_peer

    try:
        remote = fetch_peer(peer, "/api/sync/snapshot")
    except Exception:  # noqa: BLE001
        remote = None
    if remote is not None:
        snap = remote.get("snapshot") if isinstance(remote.get("snapshot"), dict) else remote
        return apply_remote_snap(session, peer, snap, force=force, via="wifi")
    remote = fetch_theirs(peer)
    snap = remote.get("snapshot") if remote and isinstance(remote.get("snapshot"), dict) else None
    if snap is None:
        raise ValueError(
            "Could not reach them on Wi‑Fi or the internet path. "
            "Leave both apps open and pair once on the same Wi‑Fi if they are not linked yet."
        )
    return apply_remote_snap(session, peer, snap, force=force, via="mailbox")


def _tick_locked(*, publish: bool = False) -> None:
    global _last_error
    if not setting("auto_sync"):
        return
    from app.live_sync import migrate_last_agreed

    state = load_state()
    peers = state.get("peers") or []
    if not peers:
        return
    session = _session()
    try:
        migrate_last_agreed(session)
        local = dump_snapshot(session)
        last = state.get("last_agreed") or ""
        # Always put our log on the mailbox so the other computer can catch up.
        try:
            publish_ours(session)
        except Exception as exc:  # noqa: BLE001
            _last_error = str(exc)
        for peer in peers:
            try_lan(session, peer, local, last)
            local = dump_snapshot(session)
            last = load_state().get("last_agreed") or last
            # Wi‑Fi can look fine and still miss a save. Mailbox is the backup every tick.
            try_mailbox(session, peer, local, last)
            local = dump_snapshot(session)
            last = load_state().get("last_agreed") or last
    finally:
        session.close()


def tick() -> None:
    if not _lock.acquire(blocking=False):
        return
    try:
        _tick_locked(publish=True)
    finally:
        _lock.release()


def notify_after_save() -> None:
    _wakeup.set()
    threading.Thread(target=_after_save, name="mbd-replicate", daemon=True).start()


def _after_save() -> None:
    if not _lock.acquire(timeout=10):
        _wakeup.set()
        return
    try:
        _tick_locked(publish=True)
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
