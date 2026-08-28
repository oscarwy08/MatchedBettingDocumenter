#!/usr/bin/env python3
"""Install a new zip over this folder without touching data/ or .venv/."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

KEEP_TOP = {"data", ".venv", "venv"}
SKIP_NAMES = {".git", "__pycache__", ".pytest_cache", "dist", ".env"}

ROOT = Path(__file__).resolve().parent


def _pick_zip() -> Path | None:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.wm_attributes("-topmost", True)
        chosen = filedialog.askopenfilename(
            title="Choose the new MatchedBettingDocumenter.zip",
            filetypes=[("Zip files", "*.zip"), ("All files", "*.*")],
        )
        root.destroy()
        if chosen:
            return Path(chosen)
    except Exception:
        pass
    raw = input("Path to MatchedBettingDocumenter.zip: ").strip().strip('"')
    return Path(raw) if raw else None


def main() -> int:
    if len(sys.argv) >= 2:
        zip_path = Path(sys.argv[1]).expanduser().resolve()
    else:
        print("Installs the new app over this folder and keeps data/ (your bets) and .venv.")
        picked = _pick_zip()
        if picked is None:
            print("No zip chosen.")
            return 1
        zip_path = picked.expanduser().resolve()
    if not zip_path.is_file():
        print(f"Zip not found: {zip_path}")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(tmp_path)
        run_py = next(tmp_path.rglob("run.py"), None)
        if run_py is None:
            print("That zip does not look like Matched Betting Documenter.")
            return 1
        _copy_overlay(run_py.parent, ROOT)

    _install_requirements()
    print("Updated. Your bets in data/ were left alone. Run ./start.sh or start.bat")
    try:
        zip_path.unlink()
        print(f"Deleted {zip_path.name}.")
    except OSError as exc:
        print(f"Could not delete the zip: {exc}")
    return 0


def _copy_overlay(src: Path, dest: Path, *, top: bool = True) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name in SKIP_NAMES:
            continue
        if top and item.name in KEEP_TOP:
            continue
        target = dest / item.name
        if item.is_dir():
            _copy_overlay(item, target, top=False)
        else:
            shutil.copy2(item, target)


def _install_requirements() -> None:
    req = ROOT / "requirements.txt"
    if not req.exists():
        return
    candidates = [
        ROOT / ".venv" / "bin" / "python",
        ROOT / ".venv" / "Scripts" / "python.exe",
    ]
    python = next((path for path in candidates if path.exists()), None)
    if python is None:
        return
    subprocess.check_call([str(python), "-m", "pip", "install", "-q", "-r", str(req)])


if __name__ == "__main__":
    raise SystemExit(main())
