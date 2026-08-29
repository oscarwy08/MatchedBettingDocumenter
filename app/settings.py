"""Machine-local options in data/settings.json. Updates never overwrite this."""

from __future__ import annotations

import json
import os
from pathlib import Path

BOOL_KEYS = {
    "open_browser",
    "update_on_start",
    "update_popup",
    "allow_lan",
    "excel_sync",
    "auto_sync",
}

DEFAULTS = {
    "open_browser": True,
    "update_on_start": True,
    "update_popup": True,
    "allow_lan": True,
    "excel_sync": True,
    "auto_sync": True,
    "port": 5050,
    "default_exchange_id": None,
}


def settings_path() -> Path:
    env = (os.environ.get("MBD_ROOT") or "").strip()
    root = Path(env).expanduser().resolve() if env else Path(__file__).resolve().parent.parent
    return root / "data" / "settings.json"


def parse_port(raw) -> int:
    try:
        port = int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("Port must be a number.") from exc
    if port < 1024 or port > 65535:
        raise ValueError("Port must be between 1024 and 65535.")
    return port


def load() -> dict:
    path = settings_path()
    raw: dict = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw = loaded
        except (OSError, json.JSONDecodeError):
            raw = {}
    out = {key: default for key, default in DEFAULTS.items()}
    for key, default in DEFAULTS.items():
        if key in raw:
            out[key] = _coerce(key, raw[key], default)
    if "auto_update" in raw:
        if "update_on_start" not in raw:
            out["update_on_start"] = bool(raw["auto_update"])
        if "update_popup" not in raw:
            out["update_popup"] = bool(raw["auto_update"])
    return out


def get(key: str):
    if key not in DEFAULTS:
        raise KeyError(key)
    return load()[key]


def save(updates: dict) -> dict:
    current = load()
    for key in DEFAULTS:
        if key in updates:
            current[key] = _coerce(key, updates[key], DEFAULTS[key])
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    return current


def _coerce(key: str, value, default):
    if key in BOOL_KEYS:
        return bool(value)
    if key == "port":
        try:
            return parse_port(value)
        except ValueError:
            return default
    if key == "default_exchange_id":
        if value in (None, "", 0, "0"):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return default
