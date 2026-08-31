#!/usr/bin/env python3
"""Install a new zip over this folder without touching data/ or .venv/.

  update.py path/to.zip     overlay from a local zip (old zip is deleted)
  update.py --auto          read latest.txt on GitHub and update if newer
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

KEEP_TOP = {"data", ".venv", "venv"}
SKIP_NAMES = {".git", "__pycache__", ".pytest_cache", "dist", ".env"}
FRONT_FILES = ("start.sh", "start.bat", "README.txt")
MOVE_INTO_PROGRAM = ("app", "run.py", "update.py", "requirements.txt", ".venv", "venv")
OBSOLETE_FILES = (
    "update.sh",
    "update.bat",
    "pack.sh",
    "release.sh",
    "release.bat",
    "release.py",
    "pytest.ini",
    ".gitignore",
    "README.md",
    "Start.sh",
    "Start.command",
    "allow-firewall.bat",
    "allow-firewall.sh",
)
OBSOLETE_DIRS = ("tests", "share", "__pycache__", ".pytest_cache")
USER_AGENT = "MatchedBettingDocumenter"
ASSET_NAME = "MatchedBettingDocumenter.zip"

ROOT = Path(__file__).resolve().parent


def install_root() -> Path:
    env = (os.environ.get("MBD_ROOT") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if ROOT.name == "program" and (
        (ROOT.parent / "start.sh").is_file()
        or (ROOT.parent / "start.bat").is_file()
        or (ROOT.parent / "Start.sh").is_file()
        or (ROOT.parent / "Start.bat").is_file()
    ):
        return ROOT.parent
    return ROOT


def parse_version(raw: str) -> tuple[int, ...]:
    text = raw.strip().lstrip("vV")
    parts: list[int] = []
    for chunk in text.split("."):
        digits = ""
        for char in chunk:
            if char.isdigit():
                digits += char
            else:
                break
        if digits:
            parts.append(int(digits))
    return tuple(parts) or (0,)


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def current_version() -> str:
    from app.version import VERSION

    return VERSION


def configured_repo() -> str:
    env = (os.environ.get("MBD_UPDATE_REPO") or "").strip()
    if env:
        return env
    override = install_root() / "data" / "update_repo"
    if override.is_file():
        text = override.read_text(encoding="utf-8").strip()
        if text:
            return text
    try:
        from app.update_source import UPDATE_REPO

        return (UPDATE_REPO or "").strip()
    except Exception:
        return ""


def _auth_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    }
    token = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    token_file = install_root() / "data" / "github_token"
    if not token and token_file.is_file():
        token = token_file.read_text(encoding="utf-8").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _http_json(url: str) -> dict:
    request = urllib.request.Request(url, headers=_auth_headers())
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))


def _http_download(url: str, dest: Path) -> None:
    headers = {"User-Agent": USER_AGENT}
    if "api.github.com" in url:
        headers.update(_auth_headers())
        headers["Accept"] = "application/octet-stream"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response, dest.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def latest_txt_url(repo: str) -> str:
    return f"https://raw.githubusercontent.com/{repo}/HEAD/latest.txt"


def latest_zip_url(repo: str) -> str:
    return f"https://github.com/{repo}/releases/latest/download/{ASSET_NAME}"


def parse_latest_text(raw: str) -> str | None:
    text = (raw or "").strip()
    if not text:
        return None
    return text.split()[0].lstrip("vV") or None


def fetch_published_version(repo: str) -> str | None:
    request = urllib.request.Request(
        latest_txt_url(repo),
        headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"},
    )
    try:
        with urllib.request.urlopen(request, timeout=6) as response:
            return parse_latest_text(response.read(64).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def fetch_latest_release(repo: str) -> dict | None:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        payload = _http_json(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    tag = str(payload.get("tag_name") or "").strip()
    assets = payload.get("assets") or []
    asset = next((item for item in assets if item.get("name") == ASSET_NAME), None)
    if asset is None:
        asset = next((item for item in assets if str(item.get("name") or "").endswith(".zip")), None)
    if not tag or not asset:
        return None
    return {
        "tag": tag,
        "version": tag.lstrip("vV"),
        "name": payload.get("name") or tag,
        "zip_url": asset.get("url"),
        "zip_name": asset.get("name"),
    }


def _package_dir() -> Path:
    front = install_root()
    nested = front / "program"
    if (nested / "run.py").is_file():
        return nested
    return ROOT


def apply_zip(zip_path: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(tmp_path)
        run_py = next(tmp_path.rglob("run.py"), None)
        if run_py is None:
            raise ValueError("That zip does not look like Matched Betting Documenter.")
        src_pkg = run_py.parent
        dest_front = install_root()
        dest_front.mkdir(parents=True, exist_ok=True)
        dest_pkg = dest_front / "program"
        _copy_overlay(src_pkg, dest_pkg)
        front = src_pkg.parent if src_pkg.name == "program" else src_pkg
        for name in FRONT_FILES:
            src = front / name
            if src.is_file():
                _install_front_file(src, dest_front / name)
    migrate_layout()
    if not _is_dev_checkout():
        _hide_internal(install_root())
    _install_requirements()


def migrate_layout() -> None:
    """Tuck a leftover 'lots of files' install into program/ and drop junk."""
    if _is_dev_checkout():
        return
    front = install_root()
    pkg = front / "program"
    for name in MOVE_INTO_PROGRAM:
        src = front / name
        dest = pkg / name
        if not src.exists():
            continue
        try:
            if src.resolve() == dest.resolve():
                continue
        except OSError:
            continue
        pkg.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            if src.is_dir():
                shutil.rmtree(src)
            else:
                src.unlink()
        else:
            shutil.move(str(src), str(dest))
    for name in OBSOLETE_FILES:
        path = front / name
        if path.is_file():
            path.unlink()
    for name in OBSOLETE_DIRS:
        path = front / name
        if path.is_dir():
            shutil.rmtree(path)
    _remove_if_distinct(front / "Start.bat", front / "start.bat")
    _hide_internal(front)


def _install_front_file(src: Path, dest: Path) -> None:
    """Replace a launcher without rewriting the inode of a running start.sh."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.tmp")
    shutil.copy2(src, tmp)
    if dest.suffix in {".sh", ".command"} or dest.name.endswith(".sh"):
        mode = tmp.stat().st_mode | 0o111
        tmp.chmod(mode)
    os.replace(tmp, dest)
    if dest.suffix in {".sh", ".command"} or dest.name.endswith(".sh"):
        dest.chmod(dest.stat().st_mode | 0o111)


def _remove_if_distinct(extra: Path, keep: Path) -> None:
    if not extra.exists():
        return
    try:
        if keep.exists() and extra.resolve() == keep.resolve():
            return
    except OSError:
        return
    if not keep.exists():
        extra.rename(keep)
        return
    extra.unlink()


def _drop_other_os_launcher(front: Path) -> None:
    if os.name == "nt":
        for name in ("start.sh", "Start.sh", "Start.command"):
            path = front / name
            if path.is_file():
                path.unlink()
        return
    for name in ("start.bat", "Start.bat", "Start.command", "Start.sh"):
        path = front / name
        if not path.is_file():
            continue
        keep = front / "start.sh"
        try:
            if keep.exists() and path.resolve() == keep.resolve():
                continue
        except OSError:
            pass
        path.unlink()


def _hide_internal(front: Path) -> None:
    _drop_other_os_launcher(front)
    if os.name == "nt":
        subprocess.run(["attrib", "+h", str(front / "program")], check=False, capture_output=True)
        for name in ("start.bat", "README.txt"):
            path = front / name
            if path.is_file():
                subprocess.run(["attrib", "-h", "-s", str(path)], check=False, capture_output=True)
        return
    if (front / "program" / "run.py").is_file():
        (front / ".hidden").write_text("program\n", encoding="utf-8")
    if sys.platform == "darwin" and (front / "program").exists():
        subprocess.run(["chflags", "hidden", str(front / "program")], check=False, capture_output=True)
    launcher = front / "start.sh"
    if launcher.is_file():
        launcher.chmod(launcher.stat().st_mode | 0o111)


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
    pkg = _package_dir()
    req = pkg / "requirements.txt"
    if not req.exists():
        return
    candidates = [
        pkg / ".venv" / "bin" / "python",
        pkg / ".venv" / "Scripts" / "python.exe",
    ]
    python = next((path for path in candidates if path.exists()), None)
    if python is None:
        return
    subprocess.check_call([str(python), "-m", "pip", "install", "-q", "-r", str(req)])


def _is_dev_checkout() -> bool:
    return (ROOT / ".git").is_dir() or (install_root() / ".git").is_dir()


def _local_zip() -> Path | None:
    seen: set[Path] = set()
    for folder in (install_root(), ROOT):
        candidate = (folder / ASSET_NAME).resolve()
        if candidate in seen or not candidate.is_file():
            continue
        seen.add(candidate)
        return candidate
    return None


def auto_update(*, force: bool = False) -> int:
    if _is_dev_checkout() and not force:
        print("Skipping auto-update (this is a git checkout).")
        return 0
    if os.environ.get("MBD_SKIP_UPDATE") == "1":
        print("Skipping auto-update (MBD_SKIP_UPDATE=1).")
        migrate_layout()
        return 0
    try:
        from app.settings import get as setting

        if not setting("update_on_start"):
            print("Skipping auto-update (turned off in Settings).")
            migrate_layout()
            return 0
    except Exception:
        pass
    local = _local_zip()
    if local is not None:
        print(f"Installing {local.name}…")
        try:
            apply_zip(local)
            local.unlink()
            print("Updated from the zip next to Start. Your bets in data/ were left alone.")
        except Exception as exc:  # noqa: BLE001
            print(f"Update failed; starting the current version. ({exc})")
            migrate_layout()
        return 0
    repo = configured_repo()
    if not repo:
        migrate_layout()
        return 0
    current = current_version()
    try:
        latest_ver = fetch_published_version(repo)
    except Exception as exc:  # noqa: BLE001
        print(f"Update check skipped: {exc}")
        migrate_layout()
        return 0
    if latest_ver is None:
        print(f"No latest.txt yet on {repo}.")
        migrate_layout()
        return 0
    if not is_newer(latest_ver, current):
        print(f"Up to date ({current}).")
        migrate_layout()
        return 0
    print(f"Updating {current} → {latest_ver} from {repo}…")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / ASSET_NAME
            _http_download(latest_zip_url(repo), zip_path)
            apply_zip(zip_path)
    except Exception as exc:  # noqa: BLE001
        print(f"Update failed; starting the current version. ({exc})")
        migrate_layout()
        return 0
    print("Updated. Your bets in data/ were left alone.")
    return 0


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


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--auto" in args:
        return auto_update(force="--force" in args)
    if "--migrate" in args:
        migrate_layout()
        return 0
    if len(args) >= 1 and not args[0].startswith("-"):
        zip_path = Path(args[0]).expanduser().resolve()
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
    try:
        apply_zip(zip_path)
    except ValueError as exc:
        print(exc)
        return 1
    print("Updated. Your bets in data/ were left alone. Click Start again.")
    try:
        zip_path.unlink()
        print(f"Deleted {zip_path.name}.")
    except OSError as exc:
        print(f"Could not delete the zip: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
