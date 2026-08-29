"""User data lives under data/ and is never overwritten by app updates."""

from __future__ import annotations

import os
from pathlib import Path


def root_dir() -> Path:
    env = (os.environ.get("MBD_ROOT") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    path = root_dir() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path
