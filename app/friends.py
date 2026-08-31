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
    from decimal import InvalidOperation

    if value is None or value == "":
        return "0.00"
    try:
        return f"{Decimal(str(value)):.2f}"
    except InvalidOperation:
        return "0.00"


def _when(value) -> str:
    from app.dates import format_uk_time

    return format_uk_time(value)


def _day(value) -> str:
    from app.dates import format_uk

    if value is None or value == "":
        return ""
    if hasattr(value, "strftime") and not hasattr(value, "hour"):
        return format_uk(value)
    text = str(value)
    return text.split(" ", 1)[0] if text else ""


def _health_row(health: dict | None) -> dict | None:
    if not health:
        return None
    return {
        "level": health.get("level") or "green",
        "label": health.get("label") or "Healthy",
        "percent": int(health.get("percent") or 0),
        "qualifiers": int(health.get("qualifiers") or 0),
        "mugs": int(health.get("mugs") or 0),
        "promo_since": int(health.get("promo_since") or 0),
        "last_mug_on": _day(health.get("last_mug_on")),
        "checked_today": bool(health.get("checked_today")),
        "last_checked_on": _day(health.get("last_checked_on")),
    }


def _spark_row(spark: dict | None) -> dict:
    from app.charts import empty_spark

    row = spark or empty_spark()
    return {
        "points": row.get("points") or "",
        "area": row.get("area") or "",
        "down": bool(row.get("down")),
    }


def _offer_row(offer) -> dict:
    from app.services import offer_snapshot

    row = offer_snapshot(offer)
    bookie_name = ""
    try:
        bookie_name = offer.bookie.name if offer.bookie is not None else ""
    except Exception:  # noqa: BLE001
        bookie_name = ""
    nxt = offer.next_reload_on
    return {
        "id": offer.id,
        "name": offer.name or "—",
        "bookie": bookie_name,
        "bookie_id": offer.bookie_id,
        "type": offer.type or "",
        "notes": offer.notes or "",
        "status": row["status"],
        "deposited": _money(row["deposited"]),
        "net_profit": _money(row["net_profit"]),
        "bookie_profit": _money(row["bookie_profit"]),
        "exchange_profit": _money(row["exchange_profit"]),
        "expected_pending": _money(row["expected_pending"]),
        "free_funds": _money(row["free_funds"]),
        "free_funds_used": _money(row["free_funds_used"]),
        "pending_count": int(row["pending_count"]),
        "leg_count": int(row["leg_count"]),
        "reload_frequency": offer.reload_frequency or "",
        "reload_stake": _money(row["reload_stake"]),
        "reload_reward": _money(row["reload_reward"]),
        "next_reload_on": _when(nxt) if nxt else "",
        "reload_due": bool(row["reload_due"]),
    }


def _transfer_row(transfer) -> dict:
    account_name = ""
    offer_name = ""
    try:
        account_name = transfer.account.name if transfer.account is not None else ""
    except Exception:  # noqa: BLE001
        account_name = ""
    try:
        offer_name = transfer.offer.name if transfer.offer is not None else ""
    except Exception:  # noqa: BLE001
        offer_name = ""
    return {
        "id": transfer.id,
        "account_id": transfer.account_id,
        "account": account_name,
        "kind": transfer.kind or "",
        "amount": _money(transfer.amount),
        "date": _day(transfer.date),
        "notes": transfer.notes or "",
        "offer_id": transfer.offer_id,
        "offer": offer_name,
    }


def _task_row(task) -> dict:
    return {
        "id": task.id,
        "account_id": task.account_id,
        "due_on": _day(task.due_on),
        "note": task.note or "",
        "done": bool(task.done),
    }


def _accounts_payload(session) -> list[dict]:
    from collections import defaultdict

    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.charts import account_sparklines, apply_sparklines
    from app.health import attach_health, today_board
    from app.models import Account, AccountTask
    from app.services import account_snapshot, account_usage

    accounts = list(session.scalars(select(Account).order_by(Account.name)))
    snaps = [account_snapshot(session, account) for account in accounts]
    apply_sparklines(snaps, account_sparklines(session))
    attach_health(snaps, today_board(session)["health_by_id"])
    tasks_by: dict[int, list] = defaultdict(list)
    for task in session.scalars(
        select(AccountTask).options(selectinload(AccountTask.account)).order_by(AccountTask.due_on, AccountTask.id)
    ):
        tasks_by[task.account_id].append(task)
    rows = []
    for snap in snaps:
        account = snap["account"]
        usage = account_usage(session, account.id)
        rows.append(
            {
                "id": account.id,
                "name": account.name,
                "type": account.type,
                "is_bookie": bool(account.is_bookie),
                "commission_percent": str(account.commission_percent or 0),
                "priority": bool(account.priority),
                "restriction": account.restriction or "",
                "notes": account.notes or "",
                "check_weekday": account.check_weekday,
                "last_checked_on": _day(account.last_checked_on),
                "opening": _money(snap["opening"]),
                "deposited": _money(snap["deposited"]),
                "withdrawn": _money(snap["withdrawn"]),
                "bookie_profit": _money(snap["bookie_profit"]),
                "exchange_profit": _money(snap["exchange_profit"]),
                "net_profit": _money(snap["net_profit"]),
                "balance": _money(snap["balance"]),
                "bets": int(usage.get("bets") or 0),
                "offers": int(usage.get("offers") or 0),
                "health": _health_row(snap.get("health")),
                "spark": _spark_row(snap.get("spark")),
                "tasks": [_task_row(task) for task in tasks_by.get(account.id, [])],
            }
        )
    return rows


def _today_payload(board: dict) -> dict:
    routine = []
    for row in board.get("routine") or []:
        account = row.get("account")
        routine.append(
            {
                "account_id": getattr(account, "id", None),
                "name": getattr(account, "name", "") or "",
                "health": _health_row(row.get("health")),
                "checked_today": bool(row.get("checked_today")),
                "priority": bool(getattr(account, "priority", False)),
                "restriction": getattr(account, "restriction", "") or "",
                "notes": getattr(account, "notes", "") or "",
                "reload_due": bool(row.get("reload_due")),
                "tasks_due": bool(row.get("tasks_due")),
            }
        )
    specials = []
    for item in board.get("specials") or []:
        account = item.get("account")
        offer = item.get("offer")
        specials.append(
            {
                "kind": item.get("kind") or "",
                "account_id": getattr(account, "id", None),
                "account": getattr(account, "name", "") or "",
                "name": item.get("name") or "",
                "detail": _day(item.get("detail")),
                "offer_id": getattr(offer, "id", None),
            }
        )
    week = []
    for day in board.get("week") or []:
        week.append(
            {
                "label": day.get("label") or "",
                "count": int(day.get("count") or 0),
                "today": bool(day.get("today")),
                "future": bool(day.get("future")),
                "href": day.get("href") or "",
            }
        )
    return {
        "today": _day(board.get("today")),
        "target": int(board.get("target") or 0),
        "checked_count": int(board.get("checked_count") or 0),
        "clean": bool(board.get("clean")),
        "routine": routine,
        "specials": specials,
        "week": week,
    }


def _charts_payload(session) -> dict:
    from app.charts import profit_series, visualiser_payload

    views = (
        "profit_time",
        "by_bookie",
        "by_exchange",
        "by_offer_type",
        "by_bet_type",
        "by_offer",
        "cashflow",
        "balances",
    )
    charts = {}
    for view in views:
        if view == "profit_time":
            charts[view] = profit_series(session, range_key="1W")
        else:
            charts[view] = visualiser_payload(session, view=view, range_key="1W")
    return charts


def _bet_row(bet) -> dict:
    from app.models import BetStatus

    pending = bet.status == BetStatus.PENDING
    profit = bet.expected_profit if pending else bet.actual_profit
    bookie = ""
    exchange = ""
    offer = ""
    try:
        bookie = bet.bookie.name if bet.bookie is not None else ""
    except Exception:  # noqa: BLE001
        bookie = ""
    try:
        exchange = bet.exchange.name if bet.exchange is not None else ""
    except Exception:  # noqa: BLE001
        exchange = ""
    try:
        offer = bet.offer.name if bet.offer is not None else ""
    except Exception:  # noqa: BLE001
        offer = ""
    return {
        "id": bet.id,
        "placed": _when(bet.placed_at or bet.date_placed),
        "date": _when(bet.placed_at or bet.date_placed),
        "starts": _when(bet.starts_at) if bet.starts_at else "",
        "starts_at": bet.starts_at.isoformat(timespec="minutes") if bet.starts_at else "",
        "settled": _when(bet.settled_at) if bet.settled_at else "",
        "event": bet.event or "—",
        "fixture_source": bet.fixture_source or "",
        "fixture_id": bet.fixture_id or "",
        "market": bet.market or "",
        "notes": bet.notes or "",
        "bet_type": bet.bet_type or "",
        "status": bet.status or "",
        "bookie": bookie,
        "bookie_id": bet.bookie_id,
        "exchange": exchange,
        "exchange_id": bet.exchange_id,
        "offer": offer,
        "offer_id": bet.offer_id,
        "back_stake": _money(bet.back_stake),
        "back_odds": str(bet.back_odds or ""),
        "lay_stake": _money(bet.lay_stake),
        "lay_odds": str(bet.lay_odds or ""),
        "commission_percent": _money(bet.commission_percent),
        "cashback": _money(bet.cashback),
        "liability": _money(bet.liability),
        "expected_profit": _money(bet.expected_profit),
        "expected_bookie_back": _money(bet.expected_bookie_back),
        "expected_exchange_back": _money(bet.expected_exchange_back),
        "expected_bookie_lay": _money(bet.expected_bookie_lay),
        "expected_exchange_lay": _money(bet.expected_exchange_lay),
        "actual_profit": _money(bet.actual_profit) if bet.actual_profit is not None else "",
        "actual_bookie_profit": _money(bet.actual_bookie_profit) if bet.actual_bookie_profit is not None else "",
        "actual_exchange_profit": _money(bet.actual_exchange_profit) if bet.actual_exchange_profit is not None else "",
        "profit": _money(profit),
        "pending": pending,
        "free_bet_returned": bool(getattr(bet, "free_bet_returned", False)),
    }


def view_dto(session: Session, *, nickname: str) -> dict:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.health import today_board
    from app.models import Bet, Offer, Transfer
    from app.services import dashboard_stats

    stats = dashboard_stats(session)
    bets = [
        _bet_row(bet)
        for bet in session.scalars(
            select(Bet)
            .options(selectinload(Bet.bookie), selectinload(Bet.exchange), selectinload(Bet.offer))
            .order_by(Bet.date_placed.desc(), Bet.id.desc())
        )
    ]
    bookies = []
    for snap in stats.get("profit_by_bookie") or []:
        account = snap["account"]
        bookies.append(
            {
                "id": account.id,
                "name": account.name,
                "net_profit": _money(snap["net_profit"]),
                "deposited": _money(snap["deposited"]),
                "bookie_profit": _money(snap["bookie_profit"]),
                "exchange_profit": _money(snap["exchange_profit"]),
                "balance": _money(snap["balance"]),
            }
        )
    offers = [
        _offer_row(offer)
        for offer in session.scalars(
            select(Offer)
            .options(selectinload(Offer.bets), selectinload(Offer.bookie))
            .order_by(Offer.created_at.desc())
        )
    ]
    transfers = [
        _transfer_row(transfer)
        for transfer in session.scalars(
            select(Transfer)
            .options(selectinload(Transfer.account), selectinload(Transfer.offer))
            .order_by(Transfer.date.desc(), Transfer.id.desc())
            .limit(80)
        )
    ]
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
        "offers": offers,
        "bets": bets,
        "recent_bets": bets[:RECENT_BETS],
        "accounts": _accounts_payload(session),
        "transfers": transfers,
        "today": _today_payload(today_board(session)),
        "charts": _charts_payload(session),
    }


def _blank(value) -> bool:
    return value is None or value == ""


def display_bet(bet: dict | None) -> dict:
    """Fill expected/actual from older friend views that only sent `profit`."""
    if not isinstance(bet, dict):
        return {}
    row = dict(bet)
    profit = row.get("profit")
    pending = bool(row.get("pending") or row.get("status") == "pending")
    if _blank(row.get("expected_profit")) and not _blank(profit) and pending:
        row["expected_profit"] = profit
    if _blank(row.get("actual_profit")) and not _blank(profit) and not pending:
        row["actual_profit"] = profit
    return row


def bet_from_view(view: dict | None, bet_id: str) -> dict | None:
    if not isinstance(view, dict):
        return None
    for bet in list(view.get("bets") or []) + list(view.get("recent_bets") or []):
        if not isinstance(bet, dict):
            continue
        if str(bet.get("id")) == str(bet_id):
            return display_bet(bet)
    return None


def offer_from_view(view: dict | None, offer_id: str) -> dict | None:
    if not isinstance(view, dict):
        return None
    for offer in view.get("offers") or []:
        if isinstance(offer, dict) and str(offer.get("id")) == str(offer_id):
            return offer
    return None


def account_from_view(view: dict | None, account_id: str) -> dict | None:
    if not isinstance(view, dict):
        return None
    for account in view.get("accounts") or []:
        if isinstance(account, dict) and str(account.get("id")) == str(account_id):
            return account
    return None


def _nonzero(value) -> bool:
    from decimal import InvalidOperation

    try:
        return Decimal(str(value or 0)) != 0
    except InvalidOperation:
        return False


def account_is_active(account: dict) -> bool:
    return any(_nonzero(account.get(key)) for key in ("deposited", "withdrawn", "net_profit", "balance"))


def encrypt_view(secret: str, payload: dict) -> str:
    import gzip

    from app.crypto import encrypt_bytes

    raw = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
    return encrypt_bytes(secret, gzip.compress(raw))


def decrypt_view(secret: str, blob: str) -> dict:
    import gzip

    from app.crypto import decrypt_bytes

    try:
        packed = decrypt_bytes(secret, blob)
    except ValueError as exc:
        text = str(exc)
        if "not encrypted" in text:
            raise ValueError("That friend view is not encrypted.") from exc
        if "truncated" in text:
            raise ValueError("That friend view is truncated.") from exc
        raise ValueError("Could not read that friend view.") from exc
    try:
        raw = gzip.decompress(packed)
    except OSError:
        raw = packed
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("That friend view is not a dashboard.")
    return payload


def allow_rate(ip: str) -> bool:
    from app.access import allow_rate as _allow

    return _allow(ip)


def cache_path(friend_id: str) -> Path:
    return cache_dir() / f"{friend_id}.json"


def store_cache(friend_id: str, payload: dict, *, live: bool = True) -> dict:
    previous = load_cache(friend_id) or {}
    now = datetime.now().isoformat(timespec="seconds")
    record = {
        "friend_id": friend_id,
        "fetched_at": now,
        "live_at": now if live else previous.get("live_at"),
        "payload": payload,
    }
    cache_path(friend_id).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


ONLINE_WINDOW_SEC = 90
LAN_PROBE_TIMEOUT = 0.7


def _parse_stamp(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        from app.dates import parse_uk_datetime

        return parse_uk_datetime(text)


def presence_for(friend_id: str, *, live: bool | None = None) -> dict:
    """Online if we just reached them, or last seen from the cached view."""
    from app.dates import format_uk_time

    if live:
        return {"online": True, "label": "Online"}
    cached = load_cache(friend_id) if friend_id else None
    stamp = None
    if cached:
        stamp = cached.get("live_at") or cached.get("fetched_at")
    parsed = _parse_stamp(stamp)
    if parsed is None:
        return {"online": False, "label": ""}
    if live is None:
        age = (datetime.now() - parsed).total_seconds()
        if 0 <= age <= ONLINE_WINDOW_SEC:
            return {"online": True, "label": "Online"}
    return {"online": False, "label": f"Last seen {format_uk_time(parsed)}"}


def fetch_lan(friend: dict) -> dict | None:
    """Try Wi‑Fi only — no mailbox wait. Used to mark Online on the friends list."""
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
    secret = friend.get("secret")
    if not hosts or not secret:
        return None
    token = VIEW_PREFIX + str(secret)
    try:
        remote = fetch_json(hosts, "/api/friend/view", token, timeout=LAN_PROBE_TIMEOUT)
        cipher = remote.get("ciphertext") or remote.get("payload")
        if isinstance(cipher, str):
            return decrypt_view(str(secret), cipher)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return None
    return None


def attach_presence(friends: list) -> list:
    """Probe each friend on Wi‑Fi in parallel, then attach Online / last seen."""
    from concurrent.futures import ThreadPoolExecutor

    items = [item for item in friends if isinstance(item, dict) and item.get("id") and item.get("secret")]

    def probe(item: dict) -> None:
        try:
            payload = fetch_lan(item)
            if payload:
                store_cache(str(item.get("id") or ""), payload, live=True)
        except Exception:  # noqa: BLE001
            return

    if items:
        workers = min(6, len(items))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(probe, items))
    return [{**item, "presence": presence_for(str(item.get("id") or ""))} for item in friends]


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
