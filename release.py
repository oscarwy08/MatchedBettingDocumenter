#!/usr/bin/env python3
"""Pack the app and publish dist/MatchedBettingDocumenter.zip as a GitHub Release."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE_FILE = ROOT / "app" / "update_source.py"


def _run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=ROOT, check=check, text=True, capture_output=True)


def _gh(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return _run(["gh", *args], check=check)


def _write_repo(repo: str) -> None:
    SOURCE_FILE.write_text(
        '"""GitHub repo that publishes Releases with MatchedBettingDocumenter.zip.\n\n'
        "`./release.sh` fills this in the first time you publish. Installed copies\n"
        "read it so they know where to check. You can also put `owner/repo` in\n"
        "`data/update_repo` or set MBD_UPDATE_REPO.\n"
        '"""\n\n'
        f'UPDATE_REPO = "{repo}"\n',
        encoding="utf-8",
    )


def _current_repo() -> str:
    try:
        from app.update_source import UPDATE_REPO

        return (UPDATE_REPO or "").strip()
    except Exception:
        return ""


def _ensure_repo() -> str:
    repo = _current_repo()
    if repo:
        return repo
    who = _gh("api", "user", "--jq", ".login")
    login = (who.stdout or "").strip()
    if not login:
        raise SystemExit("Could not read your GitHub username. Run: gh auth login")
    repo = f"{login}/MatchedBettingDocumenter"
    exists = _gh("repo", "view", repo, check=False)
    if exists.returncode != 0:
        print(f"Creating public repo {repo} (needed so installs can download updates).")
        _gh(
            "repo",
            "create",
            repo,
            "--public",
            "--add-readme",
            "--description",
            "Matched Betting Documenter releases",
        )
    _write_repo(repo)
    print(f"Update source set to {repo}.")
    return repo


def main() -> int:
    try:
        _gh("auth", "status")
    except subprocess.CalledProcessError:
        print("GitHub CLI is not logged in. Run: gh auth login")
        return 1

    from app.version import VERSION

    repo = _ensure_repo()
    tag = f"v{VERSION}"
    listed = _gh("release", "view", tag, "--repo", repo, check=False)
    if listed.returncode == 0:
        print(f"Release {tag} already exists on {repo}. Bump app/version.py first.")
        return 1

    print("Packing zip…")
    packed = subprocess.run(["bash", str(ROOT / "pack.sh")], cwd=ROOT, text=True)
    if packed.returncode != 0:
        return packed.returncode
    zip_path = ROOT / "dist" / "MatchedBettingDocumenter.zip"
    if not zip_path.is_file():
        print("pack.sh did not produce dist/MatchedBettingDocumenter.zip")
        return 1

    print(f"Publishing {tag} to {repo}…")
    _gh(
        "release",
        "create",
        tag,
        str(zip_path),
        "--repo",
        repo,
        "--title",
        f"Matched Betting Documenter {tag}",
        "--notes",
        "Account pages have P&L and cashflow charts with £ axis labels and hover values. "
        "data/ is never overwritten.",
    )
    _publish_latest_txt(repo, VERSION)
    print(f"Published {tag}. Installed copies will pick it up the next time they start.")
    return 0


def _publish_latest_txt(repo: str, version: str) -> None:
    branch_res = _gh("api", f"repos/{repo}", "--jq", ".default_branch", check=False)
    branch = (branch_res.stdout or "").strip() or "main"
    payload = {
        "message": f"latest {version}",
        "content": base64.b64encode(f"{version}\n".encode()).decode(),
        "branch": branch,
    }
    existing = _gh("api", f"repos/{repo}/contents/latest.txt", check=False)
    if existing.returncode == 0:
        payload["sha"] = json.loads(existing.stdout)["sha"]
    pushed = subprocess.run(
        ["gh", "api", "--method", "PUT", f"repos/{repo}/contents/latest.txt", "--input", "-"],
        input=json.dumps(payload),
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if pushed.returncode != 0:
        print(f"Could not write latest.txt: {pushed.stderr or pushed.stdout}")
        return
    print(f"latest.txt on GitHub is now {version}.")


if __name__ == "__main__":
    raise SystemExit(main())
