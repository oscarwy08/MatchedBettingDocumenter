"""Once-per-version What's new card. Seen flags stay in data/ and are never synced."""

from __future__ import annotations

import json
from pathlib import Path

from app.paths import data_dir
from app.version import VERSION

# Add an entry when you bump VERSION. If a version has no entry, the card still
# appears once with a short OK note. Use fields="event_picker" only when this
# update needs the free-signup inputs.
NOTES = {
    "2.0.0": {
        "kicker": "This update",
        "title": "Pick football and racing events",
        "paragraphs": [
            "On the calculator, start typing an event. Matching Premier League, Championship, Champions League, and UK/Irish races appear. Pick one to fill the name, start time, and estimated finish. When the feed says it has finished, you get the same bell and desktop popup as a start.",
            "This is optional. Skip to type events by hand, or paste the free signups below. You can add or change them later in Settings. If you type a start time without picking a match, you still get a start alert.",
        ],
        "fields": "event_picker",
        "primary": "Enter",
        "secondary": "Skip",
    },
    "2.0.1": {
        "kicker": "This update",
        "title": "Tomorrow's races in the picker",
        "paragraphs": [
            "UK and Irish racecards now include tomorrow as well as today. Type a course, or tomorrow, to find them. The free feed does not list meetings after that; those can still be typed by hand.",
        ],
        "primary": "OK",
    },
    "2.0.2": {
        "kicker": "This update",
        "title": "Shorter racing names",
        "paragraphs": [
            "Picked horse races now use the off time and course only, such as 16:06 Brighton. Tomorrow still shows on the smaller line, and Starts still gets the real date.",
        ],
        "primary": "OK",
    },
}


def path() -> Path:
    return data_dir() / "whats_new.json"


def pending() -> bool:
    return VERSION not in seen()


def current() -> dict | None:
    if not pending():
        return None
    return note_for(VERSION)


def note_for(version: str) -> dict:
    note = NOTES.get(version)
    if note:
        return dict(note)
    return {
        "kicker": "This update",
        "title": f"Version {version}",
        "paragraphs": [f"This copy is now {version}. Your data/ folder is left alone."],
        "primary": "OK",
    }


def event_picker_complete(
    football_token_value: str | None,
    racing_user: str | None,
    racing_password: str | None,
) -> bool:
    return bool(
        (football_token_value or "").strip()
        and (racing_user or "").strip()
        and (racing_password or "").strip()
    )


def mark_seen(version: str | None = None) -> None:
    ident = (version or VERSION).strip()
    if not ident:
        return
    versions = seen()
    versions.add(ident)
    payload = _load()
    payload.pop("event_picker", None)
    payload["seen"] = sorted(versions)
    path().write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def seen() -> set[str]:
    flags = _load()
    versions: set[str] = set()
    raw = flags.get("seen")
    if isinstance(raw, list):
        versions.update(str(item).strip() for item in raw if str(item).strip())
    if flags.get("event_picker"):
        versions.add("1.9.7")
    return versions


def _load() -> dict:
    file = path()
    if not file.is_file():
        return {}
    try:
        payload = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}
