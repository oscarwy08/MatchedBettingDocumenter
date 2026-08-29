"""Own-device pairing, fingerprints, and paste-one-string share codes."""

from __future__ import annotations

import json
import random
import secrets
import socket
import time
from pathlib import Path

from app.nat import format_hosts, lan_ip
from app.paths import data_dir
from app.snapshot import fingerprint_payload, snapshot_counts

_share_pin: str | None = None


def sync_path() -> Path:
    return data_dir() / "sync.json"


def default_nickname() -> str:
    try:
        return socket.gethostname() or "This computer"
    except OSError:
        return "This computer"


def empty_state() -> dict:
    return {
        "device_id": secrets.token_hex(8),
        "nickname": default_nickname(),
        "device_token": secrets.token_urlsafe(24),
        "pair_secret": secrets.token_urlsafe(24),
        "peers": [],
        "unlinked": [],
        "last_agreed": "",
        "conflict": None,
    }


def load_state() -> dict:
    path = sync_path()
    raw: dict = {}
    if not path.is_file():
        return save_state(empty_state())
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            raw = loaded
    except (OSError, json.JSONDecodeError):
        raw = {}
    state = empty_state()
    if raw.get("device_id"):
        state["device_id"] = str(raw["device_id"])
    if raw.get("nickname"):
        state["nickname"] = str(raw["nickname"])
    if raw.get("device_token"):
        state["device_token"] = str(raw["device_token"])
    if raw.get("pair_secret"):
        state["pair_secret"] = str(raw["pair_secret"])
    if isinstance(raw.get("peers"), list):
        state["peers"] = [item for item in raw["peers"] if isinstance(item, dict)]
    if isinstance(raw.get("unlinked"), list):
        state["unlinked"] = [str(item) for item in raw["unlinked"] if item]
    if raw.get("last_agreed"):
        state["last_agreed"] = str(raw["last_agreed"])
    if isinstance(raw.get("conflict"), dict):
        state["conflict"] = raw["conflict"]
    return state


def save_state(state: dict) -> dict:
    path = sync_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return state


def ensure_state() -> dict:
    path = sync_path()
    if path.is_file():
        return load_state()
    return save_state(empty_state())


def has_paired_peers() -> bool:
    return bool(load_state().get("peers"))


def current_pin() -> str | None:
    return _share_pin


def device_token() -> str:
    return ensure_state()["device_token"]


def ensure_pair_secret() -> str:
    state = ensure_state()
    if not state.get("pair_secret"):
        state["pair_secret"] = secrets.token_urlsafe(24)
        save_state(state)
    return state["pair_secret"]


def adopt_pair_secret(secret: str) -> dict:
    """Both computers keep the same pair secret so they are equals, not parent/child."""
    text = (secret or "").strip()
    if not text:
        return ensure_state()
    state = ensure_state()
    if state.get("pair_secret") == text:
        return state
    state["pair_secret"] = text
    return save_state(state)


def start_share() -> str:
    global _share_pin
    _share_pin = f"{random.randint(0, 999999):06d}"
    ensure_pair_secret()
    return _share_pin


def stop_share() -> None:
    global _share_pin
    _share_pin = None


def make_link_code(pin: str, port: int = 5050) -> str:
    try:
        hosts = format_hosts(port)
    except Exception:  # noqa: BLE001
        hosts = f"{lan_ip()}:{port}"
    return f"{pin}@{hosts}"


def parse_link_targets(raw: str) -> tuple[str, list[str]]:
    text = (raw or "").strip().replace(" ", "")
    if "@" not in text:
        raise ValueError("Link code looks like 482193@192.168.1.10:5050")
    token, rest = text.split("@", 1)
    token = token.strip()
    rest = rest.strip()
    if not token or not rest:
        raise ValueError("The code is missing the PIN or address.")
    hosts: list[str] = []
    for part in rest.split("+"):
        host = part.strip()
        if not host:
            continue
        if ":" not in host:
            host = f"{host}:5050"
        hosts.append(host)
    if not hosts:
        raise ValueError("The code is missing the other computer's address.")
    return token, hosts


def parse_link_code(raw: str) -> tuple[str, str]:
    token, hosts = parse_link_targets(raw)
    if not token.isdigit() or len(token) != 6:
        raise ValueError("The PIN at the start of the code should be 6 digits.")
    return token, hosts[0]


def is_friend_token(token: str) -> bool:
    return (token or "").startswith("view.")


def authorize_device(token: str) -> bool:
    """PIN, our device token, or a token we issued to a paired computer — never a friend secret."""
    if not token or is_friend_token(token):
        return False
    from app.friends import is_viewer_secret

    if is_viewer_secret(token):
        return False
    if _share_pin and token == _share_pin:
        return True
    state = load_state()
    if token == state.get("device_token"):
        return True
    return any(peer.get("our_token") == token for peer in state.get("peers") or [])


def authorize_linked(token: str) -> bool:
    """Paired computer proving who they are (their device token), or any authorize_device token."""
    if authorize_device(token):
        return True
    if not token or is_friend_token(token):
        return False
    return any(peer.get("token") == token for peer in load_state().get("peers") or [])


def remember_linked_device(
    *,
    device_id: str,
    token: str,
    nickname: str = "",
    lan_host: str = "",
    port: int = 5050,
) -> dict | None:
    """Record the computer that just talked to us so pairing is two-way."""
    device_id = (device_id or "").strip()
    token = (token or "").strip()
    if not device_id or not token:
        return None
    me = ensure_state()
    if device_id == me.get("device_id") or token == me.get("device_token"):
        return None
    if is_unlinked(device_id, token):
        return None
    peer = {
        "device_id": device_id,
        "token": token,
        "nickname": (nickname or "").strip() or "Paired computer",
        "our_token": me["device_token"],
        "last_seen": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "online": True,
    }
    lan_host = (lan_host or "").strip()
    if lan_host:
        peer["lan_host"] = lan_host
        peer["port"] = int(port or 5050)
        peer["host"] = lan_host if ":" in lan_host else f"{lan_host}:{peer['port']}"
    return upsert_peer(peer)


def upsert_peer(peer: dict) -> dict:
    state = load_state()
    peers = state.get("peers") or []
    key = peer.get("device_id") or peer.get("token")
    updated = False
    for index, existing in enumerate(peers):
        match = existing.get("device_id") == peer.get("device_id") and peer.get("device_id")
        if not match:
            match = existing.get("token") == peer.get("token") and peer.get("token")
        if match:
            keep_id = existing.get("id")
            merged = {**existing, **peer}
            if keep_id:
                merged["id"] = keep_id
            peers[index] = merged
            updated = True
            break
    if not updated:
        if not peer.get("id"):
            peer["id"] = secrets.token_hex(6)
        peers.append(peer)
    state["peers"] = peers
    return save_state(state)


def is_unlinked(*keys: str) -> bool:
    blocked = set(load_state().get("unlinked") or [])
    return any(key and key in blocked for key in keys)


def allow_relink(*keys: str) -> dict:
    state = load_state()
    drop = {key for key in keys if key}
    state["unlinked"] = [item for item in (state.get("unlinked") or []) if item not in drop]
    return save_state(state)


def forget_peer(peer_id: str) -> dict:
    state = load_state()
    blocked = list(state.get("unlinked") or [])
    kept = []
    for peer in state.get("peers") or []:
        if peer.get("id") == peer_id:
            for key in (peer.get("device_id"), peer.get("token")):
                if key and key not in blocked:
                    blocked.append(str(key))
            continue
        kept.append(peer)
    state["peers"] = kept
    state["unlinked"] = blocked
    if not state["peers"]:
        state["conflict"] = None
    return save_state(state)


def set_want_push(peer_id: str, value: bool = True) -> dict:
    state = load_state()
    for peer in state.get("peers") or []:
        if peer.get("id") == peer_id:
            peer["want_push"] = bool(value)
            break
    return save_state(state)


def request_push_from_all() -> dict:
    state = load_state()
    for peer in state.get("peers") or []:
        peer["want_push"] = True
    return save_state(state)


def want_push_from() -> list[str]:
    return [
        str(peer["device_id"])
        for peer in load_state().get("peers") or []
        if peer.get("want_push") and peer.get("device_id")
    ]


def clear_want_push_for(device_id: str) -> dict:
    state = load_state()
    for peer in state.get("peers") or []:
        if peer.get("device_id") == device_id:
            peer["want_push"] = False
    return save_state(state)


def peer_by_id(peer_id: str) -> dict | None:
    for peer in load_state().get("peers") or []:
        if peer.get("id") == peer_id:
            return peer
    return None


def set_last_agreed(fingerprint: str) -> dict:
    state = load_state()
    state["last_agreed"] = fingerprint
    state["conflict"] = None
    return save_state(state)


def set_conflict(payload: dict | None) -> dict:
    state = load_state()
    state["conflict"] = payload
    return save_state(state)


def compare_fingerprints(local_fp: str, peer_fp: str, last_agreed: str) -> str:
    if local_fp == peer_fp:
        return "same"
    if not last_agreed:
        return "conflict"
    local_changed = local_fp != last_agreed
    peer_changed = peer_fp != last_agreed
    if not local_changed and peer_changed:
        return "pull"
    if local_changed and not peer_changed:
        return "wait"
    if local_changed and peer_changed:
        return "conflict"
    return "same"


def status_payload(session, *, include_token: bool = False) -> dict:
    from app.snapshot import dump_snapshot

    snap = dump_snapshot(session)
    state = ensure_state()
    from app.nat import lan_ip, local_ipv4s

    out = {
        "device_id": state["device_id"],
        "nickname": state["nickname"],
        "fingerprint": snap["fingerprint"],
        "counts": snapshot_counts(snap),
        "last_agreed": state.get("last_agreed") or "",
        "port": None,
        "lan_ip": lan_ip(),
        "lan_ips": [ip for ip in local_ipv4s() if ip and not ip.startswith("127.")],
        "pair_secret": state.get("pair_secret") or "",
        "want_push_from": want_push_from(),
    }
    if include_token:
        out["token"] = state["device_token"]
    return out


def local_meta(session) -> dict:
    from app.snapshot import dump_snapshot

    snap = dump_snapshot(session)
    return {
        "fingerprint": fingerprint_payload(snap),
        "counts": snapshot_counts(snap),
        "snapshot": snap,
    }
