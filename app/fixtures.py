"""Cached football and UK/IRE racing fixtures for the event picker."""

from __future__ import annotations

import base64
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from app.dates import format_iso_datetime, format_uk_time, local_now
from app.paths import data_dir

FOOTBALL_PAD = timedelta(hours=2, minutes=5)
RACING_PAD = timedelta(minutes=15)
FOOTBALL_STALE = timedelta(minutes=30)
FOOTBALL_LIVE_STALE = timedelta(minutes=5)
RACING_CARDS_STALE = timedelta(hours=1)
RACING_RESULTS_STALE = timedelta(minutes=3)

FOOTBALL_URL = "https://api.football-data.org/v4/matches"
RACING_CARDS_URL = "https://api.theracingapi.com/v1/racecards/free"
RACING_RESULTS_URL = "https://api.theracingapi.com/v1/results/today/free"

_UK_REGIONS = {"gb", "ire", "uk", "ireland", "great britain"}
_FINISHED = {"FINISHED", "AWARDED"}
_lock = threading.Lock()
USER_AGENT = "MatchedBettingDocumenter/2.0.0"


def football_token_path() -> Path:
    return data_dir() / "football_token"


def racing_creds_path() -> Path:
    return data_dir() / "racing_api.json"


def cache_path() -> Path:
    return data_dir() / "fixtures_cache.json"


def football_token() -> str:
    path = football_token_path()
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def racing_creds() -> tuple[str, str]:
    path = racing_creds_path()
    if not path.is_file():
        return "", ""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return "", ""
    if not isinstance(raw, dict):
        return "", ""
    return str(raw.get("username") or "").strip(), str(raw.get("password") or "").strip()


def configured() -> dict:
    user, password = racing_creds()
    return {"football": bool(football_token()), "racing": bool(user and password)}


def save_tokens(*, football_token_value: str | None, racing_user: str | None, racing_password: str | None) -> None:
    token = (football_token_value or "").strip()
    if token:
        football_token_path().write_text(token + "\n", encoding="utf-8")
    user = (racing_user or "").strip()
    password = racing_password if racing_password is not None else ""
    old_user, old_password = racing_creds()
    if not user and not (password or "").strip():
        return
    racing_creds_path().write_text(
        json.dumps(
            {
                "username": user or old_user,
                "password": (password.strip() if password.strip() else old_password),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def estimate_ends(source: str | None, starts_at: datetime | None) -> datetime | None:
    if starts_at is None:
        return None
    if source == "racing":
        return starts_at + RACING_PAD
    return starts_at + FOOTBALL_PAD


def search(query: str, *, limit: int = 20, now: datetime | None = None) -> list[dict]:
    refresh(now=now)
    clock = now or local_now()
    needles = [part for part in (query or "").casefold().split() if part]
    scored: list[tuple[datetime, dict]] = []
    for item in _load_cache().get("items") or []:
        hay = " ".join(
            [
                str(item.get("label") or ""),
                str(item.get("hint") or ""),
                str(item.get("course") or ""),
            ]
        ).casefold()
        if needles and not all(part in hay for part in needles):
            continue
        start = _parse_stored(item.get("starts_at"))
        if start is None:
            continue
        scored.append((start, item))
    scored.sort(key=lambda row: (row[0] < clock - timedelta(hours=3), row[0]))
    out = []
    for start, item in scored[:limit]:
        source = item.get("source") or ""
        ends = _parse_stored(item.get("ends_at")) or estimate_ends(source, start)
        out.append(
            {
                "source": source,
                "fixture_id": str(item.get("fixture_id") or ""),
                "label": item.get("label") or "",
                "hint": item.get("hint") or "",
                "starts_at": format_iso_datetime(start),
                "ends_at": format_iso_datetime(ends),
            }
        )
    return out


def is_finished(source: str | None, fixture_id: str | None) -> bool:
    ident = str(fixture_id or "").strip()
    if not ident:
        return False
    cache = _load_cache()
    if source == "racing":
        return ident in set(cache.get("racing_finished") or [])
    for item in cache.get("items") or []:
        if item.get("source") == "football" and str(item.get("fixture_id") or "") == ident:
            return str(item.get("status") or "").upper() in _FINISHED
    return False


def refresh(
    now: datetime | None = None,
    *,
    football_live: bool = False,
    racing_results: bool = False,
) -> None:
    clock = now or local_now()
    with _lock:
        cache = _load_cache()
        items = list(cache.get("items") or [])
        if football_token() and _stale(
            cache.get("football_at"),
            FOOTBALL_LIVE_STALE if football_live else FOOTBALL_STALE,
            clock,
        ):
            fetched = _fetch_football(clock)
            if fetched is not None:
                items = _merge_football(items, fetched, clock)
                cache["football_at"] = clock.isoformat(timespec="seconds")
        user, password = racing_creds()
        if user and password and _stale(cache.get("racing_at"), RACING_CARDS_STALE, clock):
            fetched = _fetch_racing_cards(user, password)
            if fetched is not None:
                items = [row for row in items if row.get("source") != "racing"] + fetched
                cache["racing_at"] = clock.isoformat(timespec="seconds")
        if user and password and racing_results and _stale(
            cache.get("results_at"), RACING_RESULTS_STALE, clock
        ):
            finished = _fetch_racing_results(user, password)
            if finished is not None:
                cache["racing_finished"] = sorted(finished)
                cache["results_at"] = clock.isoformat(timespec="seconds")
        cache["items"] = items
        _save_cache(cache)


def _stale(stamp: str | None, max_age: timedelta, now: datetime) -> bool:
    if not stamp:
        return True
    parsed = _parse_stored(stamp)
    if parsed is None:
        return True
    return now - parsed >= max_age


def _merge_football(items: list[dict], fetched: list[dict], now: datetime) -> list[dict]:
    new_ids = {row.get("fixture_id") for row in fetched}
    kept = []
    for old in items:
        if old.get("source") != "football":
            continue
        if old.get("fixture_id") in new_ids:
            continue
        start = _parse_stored(old.get("starts_at"))
        if start is not None and now - start < timedelta(days=2):
            kept.append(old)
    others = [row for row in items if row.get("source") != "football"]
    return others + kept + fetched


def _fetch_football(now: datetime) -> list[dict] | None:
    token = football_token()
    if not token:
        return None
    start = now.date()
    end = start + timedelta(days=2)
    url = f"{FOOTBALL_URL}?dateFrom={start.isoformat()}&dateTo={end.isoformat()}"
    payload = _get_json(url, headers={"X-Auth-Token": token})
    if payload is None:
        return None
    rows = []
    for match in payload.get("matches") or []:
        ident = match.get("id")
        start_at = _to_local(match.get("utcDate"))
        if ident is None or start_at is None:
            continue
        home = _team(match.get("homeTeam"))
        away = _team(match.get("awayTeam"))
        if not home or not away:
            continue
        competition = ((match.get("competition") or {}).get("name")) or "Football"
        label = f"{home} vs {away}"
        rows.append(
            {
                "source": "football",
                "fixture_id": str(ident),
                "label": label,
                "hint": f"{competition} · {format_uk_time(start_at)}",
                "starts_at": start_at.isoformat(timespec="minutes"),
                "ends_at": (start_at + FOOTBALL_PAD).isoformat(timespec="minutes"),
                "status": str(match.get("status") or ""),
            }
        )
    return rows


def _fetch_racing_cards(user: str, password: str) -> list[dict] | None:
    rows: list[dict] = []
    seen: set[str] = set()
    for day in ("today", "tomorrow"):
        payload = _get_json(
            f"{RACING_CARDS_URL}?day={day}",
            headers=_basic(user, password),
        )
        if payload is None:
            if not rows:
                return None
            continue
        for card in payload.get("racecards") or []:
            ident = str(card.get("race_id") or "").strip()
            if not ident or ident in seen:
                continue
            region = str(card.get("region") or "").strip().casefold()
            if region and region not in _UK_REGIONS:
                continue
            start_at = _to_local(card.get("off_dt"))
            if start_at is None:
                continue
            course = str(card.get("course") or "").strip() or "Race"
            name = str(card.get("race_name") or "").strip()
            clock = start_at.strftime("%H:%M")
            label = f"{clock} {course}"
            if name:
                label = f"{label} — {name}"
            seen.add(ident)
            rows.append(
                {
                    "source": "racing",
                    "fixture_id": ident,
                    "label": label,
                    "hint": f"{course} · {format_uk_time(start_at)}",
                    "course": course,
                    "starts_at": start_at.isoformat(timespec="minutes"),
                    "ends_at": (start_at + RACING_PAD).isoformat(timespec="minutes"),
                    "status": str(card.get("race_status") or ""),
                }
            )
    return rows


def _fetch_racing_results(user: str, password: str) -> set[str] | None:
    ids: set[str] = set()
    skip = 0
    while skip < 500:
        payload = _get_json(
            f"{RACING_RESULTS_URL}?limit=100&skip={skip}",
            headers=_basic(user, password),
        )
        if payload is None:
            return None if skip == 0 else ids
        batch = payload.get("results") or []
        for row in batch:
            ident = str(row.get("race_id") or "").strip()
            if ident:
                ids.add(ident)
        if len(batch) < 100:
            break
        skip += 100
    return ids


def _team(blob) -> str:
    if not isinstance(blob, dict):
        return ""
    return str(blob.get("shortName") or blob.get("name") or "").strip()


def _to_local(raw) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        return None
    if value.tzinfo is not None:
        value = value.astimezone().replace(tzinfo=None)
    return value.replace(second=0, microsecond=0)


def _parse_stored(raw) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _basic(user: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _get_json(url: str, headers: dict[str, str] | None = None) -> dict | None:
    request_headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            raw = response.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _load_cache() -> dict:
    path = cache_path()
    if not path.is_file():
        return {"items": [], "racing_finished": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"items": [], "racing_finished": []}
    if not isinstance(payload, dict):
        return {"items": [], "racing_finished": []}
    payload.setdefault("items", [])
    payload.setdefault("racing_finished", [])
    return payload


def _save_cache(payload: dict) -> None:
    path = cache_path()
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
