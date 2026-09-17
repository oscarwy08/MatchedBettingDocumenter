"""Friends hold a pair_secret-sealed snapshot they cannot read or edit."""

from __future__ import annotations

import gzip
import json
import threading
from pathlib import Path

from sqlalchemy.orm import Session

from app.crypto import decrypt_bytes, encrypt_bytes
from app.paths import data_dir
from app.snapshot import apply_snapshot, dump_snapshot, would_shrink

KIND = "vault-hold"
POLL_EVERY_SEC = 4
MAILBOX_TIMEOUT = 2.0
LAN_TIMEOUT = 2.0

_lock = threading.Lock()
_wakeup = threading.Event()
_started = False


def vault_dir() -> Path:
    path = data_dir() / "friend_vault"
    path.mkdir(parents=True, exist_ok=True)
    return path


def blob_path(invite_id: str) -> Path:
    return vault_dir() / f"{invite_id}.mbd1"


def meta_path(invite_id: str) -> Path:
    return vault_dir() / f"{invite_id}.json"


def _session() -> Session:
    from app.db import SessionLocal

    if SessionLocal is None:
        raise RuntimeError("Database is not initialised.")
    return SessionLocal()


def meta_of(payload: dict | None) -> dict:
    if not isinstance(payload, dict):
        return {}
    counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
    return {
        "fingerprint": str(payload.get("fingerprint") or ""),
        "counts": {
            "accounts": int(counts.get("accounts") or 0),
            "offers": int(counts.get("offers") or 0),
            "bets": int(counts.get("bets") or 0),
            "transfers": int(counts.get("transfers") or 0),
        },
        "exported_at": str(payload.get("exported_at") or ""),
    }


def held_meta(invite_id: str) -> dict | None:
    path = meta_path(invite_id)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    meta = meta_of(raw)
    return meta if meta.get("fingerprint") else None


def load_held(invite_id: str) -> dict | None:
    meta = held_meta(invite_id)
    path = blob_path(invite_id)
    if meta is None or not path.is_file():
        return None
    try:
        ciphertext = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not ciphertext.startswith("mbd1."):
        return None
    return {**meta, "ciphertext": ciphertext}


def delete_held(invite_id: str) -> None:
    for path in (blob_path(invite_id), meta_path(invite_id)):
        try:
            path.unlink()
        except OSError:
            pass


def delete_all_held() -> None:
    folder = vault_dir()
    for path in folder.glob("*"):
        if path.is_file():
            try:
                path.unlink()
            except OSError:
                pass


def is_newer(incoming: dict, existing: dict | None) -> bool:
    if not incoming.get("fingerprint"):
        return False
    if existing is None or not existing.get("fingerprint"):
        return True
    if incoming.get("fingerprint") == existing.get("fingerprint"):
        return False
    return (incoming.get("exported_at") or "") >= (existing.get("exported_at") or "")


def compare_meta(local: dict, remote: dict | None) -> str:
    if not remote or not remote.get("fingerprint"):
        return "push"
    if local.get("fingerprint") == remote.get("fingerprint"):
        return "same"
    if (remote.get("exported_at") or "") > (local.get("exported_at") or ""):
        return "pull"
    return "push"


def store_if_newer(invite_id: str, envelope: dict) -> bool:
    if not invite_id or not isinstance(envelope, dict):
        return False
    ciphertext = str(envelope.get("ciphertext") or "").strip()
    if not ciphertext.startswith("mbd1."):
        return False
    incoming = meta_of(envelope)
    if not incoming.get("fingerprint"):
        return False
    if not is_newer(incoming, held_meta(invite_id)):
        return False
    blob_path(invite_id).write_text(ciphertext + "\n", encoding="utf-8")
    meta_path(invite_id).write_text(json.dumps(incoming, indent=2) + "\n", encoding="utf-8")
    return True


def seal_snapshot(payload: dict, pair_secret: str) -> dict:
    raw = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
    ciphertext = encrypt_bytes(pair_secret, gzip.compress(raw))
    return {**meta_of(payload), "ciphertext": ciphertext}


def unseal_snapshot(envelope: dict, pair_secret: str) -> dict:
    ciphertext = str((envelope or {}).get("ciphertext") or "")
    packed = decrypt_bytes(pair_secret, ciphertext)
    try:
        raw = gzip.decompress(packed)
    except OSError:
        raw = packed
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict) or "accounts" not in payload:
        raise ValueError("That spare is not a Documenter backup.")
    return payload


def encode_mailbox(envelope: dict) -> str:
    return json.dumps(envelope, separators=(",", ":"), default=str)


def decode_mailbox(blob: str | None) -> dict | None:
    if not blob:
        return None
    try:
        payload = json.loads(blob)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def apply_held(session: Session, envelope: dict, pair_secret: str) -> dict | None:
    try:
        remote = unseal_snapshot(envelope, pair_secret)
    except ValueError:
        return None
    local = dump_snapshot(session)
    if remote.get("fingerprint") and remote.get("fingerprint") == local.get("fingerprint"):
        return {"same": True, **local["counts"]}
    if would_shrink(local, remote):
        return None
    counts = apply_snapshot(session, remote, backup_why="before-friend-restore")
    session.commit()
    return {"same": False, **counts}


def ingest_mailbox_holds() -> int:
    from app.friends import load_state
    from app.mailbox import get as mailbox_get

    stored = 0
    for invite in load_state().get("invites") or []:
        secret = invite.get("secret")
        invite_id = str(invite.get("id") or "")
        if not secret or not invite_id:
            continue
        try:
            blob = mailbox_get(KIND, secret, timeout=MAILBOX_TIMEOUT)
        except Exception:  # noqa: BLE001
            continue
        envelope = decode_mailbox(blob)
        if envelope and store_if_newer(invite_id, envelope):
            stored += 1
    return stored


def _friend_hosts(friend: dict) -> list[str]:
    from app.live_sync import peer_hosts

    return peer_hosts(
        {
            "lan_host": friend.get("lan_host"),
            "wan_host": friend.get("wan_host"),
            "host": friend.get("host"),
            "port": friend.get("port") or 5050,
        }
    )


def _sync_one_friend(session: Session, friend: dict, local: dict, envelope: dict | None, pair_secret: str) -> dict:
    from urllib.error import HTTPError, URLError

    from app.friends import VIEW_PREFIX
    from app.live_sync import fetch_json, post_json
    from app.mailbox import get as mailbox_get
    from app.mailbox import put as mailbox_put

    secret = friend.get("secret")
    if not secret:
        return local
    token = VIEW_PREFIX + str(secret)
    hosts = _friend_hosts(friend)
    remote_meta = None
    mailbox_env = None
    if hosts:
        try:
            remote = fetch_json(hosts, "/api/friend/vault/meta", token, timeout=LAN_TIMEOUT)
            if isinstance(remote, dict):
                remote_meta = meta_of(remote) if remote.get("held") or remote.get("fingerprint") else None
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            remote_meta = None
    if remote_meta is None:
        try:
            mailbox_env = decode_mailbox(mailbox_get(KIND, secret, timeout=MAILBOX_TIMEOUT))
        except Exception:  # noqa: BLE001
            mailbox_env = None
        if mailbox_env:
            remote_meta = meta_of(mailbox_env)

    action = compare_meta(meta_of(local), remote_meta)
    if action == "same":
        return local
    if action == "push":
        if envelope is None:
            envelope = seal_snapshot(local, pair_secret)
        if hosts:
            try:
                post_json(hosts, "/api/friend/vault", envelope, token)
            except (HTTPError, URLError, TimeoutError, OSError, ValueError):
                pass
        try:
            mailbox_put(KIND, secret, encode_mailbox(envelope))
        except Exception:  # noqa: BLE001
            pass
        return local
    if action != "pull":
        return local
    held = None
    if hosts:
        try:
            held = fetch_json(hosts, "/api/friend/vault", token, timeout=LAN_TIMEOUT)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            held = None
    if not isinstance(held, dict) or not held.get("ciphertext"):
        held = mailbox_env
    if not isinstance(held, dict):
        return local
    applied = apply_held(session, held, pair_secret)
    if applied and not applied.get("same"):
        return dump_snapshot(session)
    return local


def _tick_locked() -> None:
    from app.friends import load_state
    from app.sync import ensure_pair_secret

    ingest_mailbox_holds()
    state = load_state()
    friends = [item for item in (state.get("friends") or []) if isinstance(item, dict) and item.get("secret")]
    if not friends:
        return
    pair_secret = ensure_pair_secret()
    session = _session()
    try:
        local = dump_snapshot(session)
        envelope = None
        for friend in friends:
            local = _sync_one_friend(session, friend, local, envelope, pair_secret)
            envelope = None
    finally:
        session.close()


def tick() -> None:
    if not _lock.acquire(blocking=False):
        return
    try:
        _tick_locked()
    finally:
        _lock.release()


def notify() -> None:
    _wakeup.set()


def _loop() -> None:
    while True:
        try:
            tick()
        except Exception:  # noqa: BLE001
            pass
        _wakeup.wait(timeout=POLL_EVERY_SEC)
        _wakeup.clear()


def start_background() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="mbd-friend-vault", daemon=True).start()
