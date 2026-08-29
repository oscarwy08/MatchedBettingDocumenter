"""Local snapshots in data/backups/ so a bad sync can be undone."""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.paths import data_dir
from app.snapshot import dump_snapshot, fingerprint_payload, snapshot_counts

MAX_AUTO = 15
AUTO_REASONS = {"before-sync", "before-restore"}


def backups_dir() -> Path:
    path = data_dir() / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _index_path() -> Path:
    return backups_dir() / "index.json"


def _load_index() -> list[dict]:
    path = _index_path()
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return raw if isinstance(raw, list) else []


def _save_index(entries: list[dict]) -> None:
    _index_path().write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")


def list_backups() -> list[dict]:
    return list(_load_index())


def save_current(session: Session, *, why: str = "manual") -> dict:
    payload = dump_snapshot(session)
    backup_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    filename = f"{backup_id}.json"
    (backups_dir() / filename).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    entry = {
        "id": backup_id,
        "file": filename,
        "time": datetime.now().isoformat(timespec="seconds"),
        "why": why,
        "counts": snapshot_counts(payload),
        "fingerprint": fingerprint_payload(payload),
    }
    entries = [entry] + _load_index()
    _save_index(entries)
    prune_auto()
    return entry


def prune_auto() -> None:
    entries = _load_index()
    autos = [item for item in entries if item.get("why") in AUTO_REASONS]
    keep_auto = {item["id"] for item in autos[:MAX_AUTO]}
    kept = []
    for item in entries:
        if item.get("why") not in AUTO_REASONS or item["id"] in keep_auto:
            kept.append(item)
            continue
        path = backups_dir() / item.get("file", "")
        if path.is_file():
            path.unlink()
    _save_index(kept)


def load_payload(backup_id: str) -> dict:
    for item in _load_index():
        if item.get("id") == backup_id:
            path = backups_dir() / item["file"]
            if not path.is_file():
                raise ValueError("That snapshot file is missing.")
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("That snapshot is not a Documenter backup.")
            return payload
    raise ValueError("Unknown snapshot.")


def restore(session: Session, backup_id: str) -> dict:
    from app.snapshot import apply_snapshot

    payload = load_payload(backup_id)
    return apply_snapshot(session, payload, backup_why="before-restore")
