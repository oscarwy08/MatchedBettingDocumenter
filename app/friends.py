"""Read-only friend viewer: encrypted dashboard DTO and last-available cache."""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from app.nat import format_hosts, lan_ip
from app.paths import data_dir

VIEW_PREFIX = "view."
RECENT_BETS = 20


def friends_state_path() -> Path:
    return data_dir() / "friends.json"


def cache_dir() -> Path:
    path = data_dir() / "friends"
    path.mkdir(parents=True, exist_ok=True)
    return path


def empty_state() -> dict:
    return {"account_name": "", "invites": [], "friends": []}


def load_state() -> dict:
    path = friends_state_path()
    if not path.is_file():
        return empty_state()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_state()
    if not isinstance(raw, dict):
        return empty_state()
    return {
        "account_name": str(raw.get("account_name") or ""),
        "invites": [item for item in raw.get("invites") or [] if isinstance(item, dict)],
        "friends": [item for item in raw.get("friends") or [] if isinstance(item, dict)],
    }


def account_name() -> str:
    name = (load_state().get("account_name") or "").strip()
    if name:
        return name
    from app.sync import default_nickname

    return default_nickname()


def export_account() -> dict:
    state = load_state()
    return {
        "account_name": state.get("account_name") or "",
        "invites": list(state.get("invites") or []),
        "friends": list(state.get("friends") or []),
    }


def apply_account(payload: dict | None) -> None:
    if not isinstance(payload, dict):
        return
    current = load_state()
    save_state(
        {
            "account_name": str(payload.get("account_name") or current.get("account_name") or ""),
            "invites": [item for item in (payload.get("invites") or []) if isinstance(item, dict)],
            "friends": [item for item in (payload.get("friends") or []) if isinstance(item, dict)],
        }
    )


def save_state(state: dict) -> dict:
    path = friends_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return state


def has_active_invite() -> bool:
    return bool(load_state().get("invites"))


def is_viewer_secret(token: str) -> bool:
    raw = token or ""
    secret = raw[len(VIEW_PREFIX) :] if raw.startswith(VIEW_PREFIX) else raw
    if not secret:
        return False
    return any(invite.get("secret") == secret for invite in load_state().get("invites") or [])


def invite_by_secret(secret: str) -> dict | None:
    raw = secret or ""
    if raw.startswith(VIEW_PREFIX):
        raw = raw[len(VIEW_PREFIX) :]
    for invite in load_state().get("invites") or []:
        if invite.get("secret") == raw:
            return invite
    return None


def create_invite(nickname: str = "") -> dict:
    state = load_state()
    invite = {
        "id": secrets.token_hex(6),
        "secret": secrets.token_urlsafe(32),
        "nickname": (nickname or "Viewer").strip() or "Viewer",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    state["invites"].append(invite)
    save_state(state)
    return invite


def revoke_invite(invite_id: str) -> dict:
    state = load_state()
    state["invites"] = [item for item in state["invites"] if item.get("id") != invite_id]
    return save_state(state)


def stop_all_invites() -> dict:
    state = load_state()
    state["invites"] = []
    return save_state(state)


def account_hosts(port: int) -> str:
    """Every address a friend can try: this computer plus paired ones we last saw."""
    try:
        hosts = format_hosts(port).split("+")
    except Exception:  # noqa: BLE001
        hosts = [f"{lan_ip()}:{port}"]
    from app.p2p import host_for
    from app.sync import load_state

    for peer in load_state().get("peers") or []:
        discovered = host_for(peer.get("device_id"))
        extras = [discovered, peer.get("lan_host"), peer.get("host")]
        peer_port = int(peer.get("port") or port)
        for raw in extras:
            if not raw:
                continue
            item = raw if ":" in str(raw) else f"{raw}:{peer_port}"
            if item not in hosts:
                hosts.append(item)
    return "+".join(hosts)


def invite_code(invite: dict, port: int) -> str:
    try:
        hosts = account_hosts(port)
    except Exception:  # noqa: BLE001
        hosts = f"{lan_ip()}:{port}"
    return f"{VIEW_PREFIX}{invite['secret']}@{hosts}"


def parse_friend_code(raw: str) -> tuple[str, list[str]]:
    from app.sync import parse_link_targets

    text = (raw or "").strip().replace(" ", "")
    if "@" not in text:
        if not text.startswith(VIEW_PREFIX):
            raise ValueError("A friend code starts with view. then a long secret.")
        secret = text[len(VIEW_PREFIX) :]
        if len(secret) < 16:
            raise ValueError("That friend code is too short.")
        return secret, []
    token, hosts = parse_link_targets(text)
    if not token.startswith(VIEW_PREFIX):
        raise ValueError("A friend code starts with view. then a long secret.")
    secret = token[len(VIEW_PREFIX) :]
    if len(secret) < 16:
        raise ValueError("That friend code is too short.")
    return secret, hosts


def upsert_friend(friend: dict) -> dict:
    state = load_state()
    friends = state["friends"]
    for index, existing in enumerate(friends):
        if existing.get("secret") == friend.get("secret"):
            friends[index] = {**existing, **friend}
            return save_state(state)
    if not friend.get("id"):
        friend["id"] = secrets.token_hex(6)
    friends.append(friend)
    return save_state(state)


def friend_by_id(friend_id: str) -> dict | None:
    for item in load_state().get("friends") or []:
        if item.get("id") == friend_id:
            return item
    return None


def forget_friend(friend_id: str) -> dict:
    state = load_state()
    state["friends"] = [item for item in state["friends"] if item.get("id") != friend_id]
    cache = cache_dir() / f"{friend_id}.json"
    if cache.is_file():
        cache.unlink()
    return save_state(state)


def _money(value) -> str:
    if value is None or value == "":
        return "0.00"
    return f"{Decimal(str(value)):.2f}"


def view_dto(session: Session, *, nickname: str) -> dict:
    from app.services import dashboard_stats

    stats = dashboard_stats(session)
    recent = []
    bets = list(stats.get("pending_bets") or [])
    # Prefer a mix: pending first already sorted; add settled by date.
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.models import Bet, BetStatus

    settled = list(
        session.scalars(
            select(Bet)
            .options(selectinload(Bet.bookie))
            .where(Bet.status != BetStatus.PENDING)
            .order_by(Bet.date_placed.desc())
            .limit(RECENT_BETS)
        )
    )
    seen = set()
    for bet in list(bets) + settled:
        if bet.id in seen:
            continue
        seen.add(bet.id)
        try:
            bookie = bet.bookie
            bookie_name = bookie.name if bookie is not None else ""
        except Exception:  # noqa: BLE001
            bookie_name = ""
        profit = bet.actual_profit if bet.status != BetStatus.PENDING else bet.expected_profit
        recent.append(
            {
                "date": (bet.placed_at or bet.date_placed).isoformat()
                if hasattr(bet.placed_at or bet.date_placed, "isoformat")
                else str(bet.date_placed),
                "event": bet.event or "—",
                "bookie": bookie_name,
                "status": bet.status,
                "profit": _money(profit),
                "pending": bet.status == BetStatus.PENDING,
            }
        )
        if len(recent) >= RECENT_BETS:
            break
    bookies = []
    for snap in stats.get("profit_by_bookie") or []:
        account = snap["account"]
        bookies.append(
            {
                "name": account.name,
                "net_profit": _money(snap["net_profit"]),
                "deposited": _money(snap["deposited"]),
            }
        )
    return {
        "nickname": nickname,
        "stats": {
            "net_profit": _money(stats["net_profit"]),
            "pending_expected": _money(stats["pending_expected"]),
            "bankroll": _money(stats["bankroll"]),
            "open_liability": _money(stats["open_liability"]),
            "month_profit": _money(stats["month_profit"]),
            "pending_count": int(stats["pending_count"]),
            "settled_count": int(stats["settled_count"]),
        },
        "profit_by_bookie": bookies,
        "recent_bets": recent,
    }


def encrypt_view(secret: str, payload: dict) -> str:
    from app.crypto import encrypt_json

    return encrypt_json(secret, payload)


def decrypt_view(secret: str, blob: str) -> dict:
    from app.crypto import decrypt_json

    try:
        payload = decrypt_json(secret, blob)
    except ValueError as exc:
        text = str(exc)
        if "not encrypted" in text:
            raise ValueError("That friend view is not encrypted.") from exc
        if "truncated" in text:
            raise ValueError("That friend view is truncated.") from exc
        raise ValueError("Could not read that friend view.") from exc
    return payload


def allow_rate(ip: str) -> bool:
    from app.access import allow_rate as _allow

    return _allow(ip)


def cache_path(friend_id: str) -> Path:
    return cache_dir() / f"{friend_id}.json"


def store_cache(friend_id: str, payload: dict) -> dict:
    record = {
        "friend_id": friend_id,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "payload": payload,
    }
    cache_path(friend_id).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def fetch_live(friend: dict) -> dict:
    from urllib.error import HTTPError, URLError

    from app.live_sync import fetch_json, peer_hosts

    hosts = peer_hosts(
        {
            "lan_host": friend.get("lan_host"),
            "wan_host": friend.get("wan_host"),
            "host": friend.get("host"),
            "port": friend.get("port") or 5050,
        }
    )
    token = VIEW_PREFIX + friend["secret"]
    last_error = ""
    if hosts:
        try:
            remote = fetch_json(hosts, "/api/friend/view", token, timeout=1.5)
            cipher = remote.get("ciphertext") or remote.get("payload")
            if isinstance(cipher, str):
                return decrypt_view(friend["secret"], cipher)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            last_error = str(exc)
    from app.mailbox import get as mailbox_get

    try:
        blob = mailbox_get("view", friend["secret"], timeout=10)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(
            f"Could not reach them on Wi‑Fi or the internet path ({exc}). "
            "Their app must be running with the invite still on."
        ) from exc
    if blob:
        return decrypt_view(friend["secret"], blob)
    raise ValueError(
        "No live view yet. Their app must be running with the invite still on — "
        "it publishes every few seconds. "
        + (f"Wi‑Fi try: {last_error}" if last_error else "")
    )


def load_cache(friend_id: str) -> dict | None:
    path = cache_path(friend_id)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None
