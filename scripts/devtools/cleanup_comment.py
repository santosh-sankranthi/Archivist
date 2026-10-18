#!/usr/bin/env python3
"""Delete bot comments on an issue whose body contains a marker (cleanup).

Usage:
  .venv/bin/python scripts/devtools/cleanup_comment.py --number 2 \
      --match "Security reports"
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env(ROOT / ".env")

from archivist.gh.client import GhError, GitHub


def _default_repo() -> str:
    slug = (os.environ.get("ARCHIVIST_GH_SLUG") or "").strip()
    if slug:
        return slug
    repos = (os.environ.get("ARCHIVIST_GH_REPOS") or "").strip()
    return repos.split("=", 1)[0].split(",")[0].strip() if repos else ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--number", type=int, required=True)
    parser.add_argument("--match", required=True)
    parser.add_argument("--repo", default="")
    args = parser.parse_args()
    token = (os.environ.get("ARCHIVIST_GH_TOKEN") or "").strip()
    slug = args.repo or _default_repo()
    if not (token and slug):
        print("need ARCHIVIST_GH_TOKEN and a repo slug")
        return 1
    gh = GitHub(token)
    login = gh.get_user().get("login") or ""
    _code, comments = gh._request(
        "GET", f"/repos/{slug}/issues/{args.number}/comments", params={"per_page": 100}
    )
    deleted = 0
    for comment in comments or []:
        author = (comment.get("user") or {}).get("login") or ""
        body = comment.get("body") or ""
        if author != login or args.match not in body:
            continue
        gh._request(
            "DELETE",
            f"/repos/{slug}/issues/comments/{comment.get('id')}",
            expect=(204,),
        )
        deleted += 1
        print(f"deleted comment {comment.get('id')} by {author}")
    print(f"deleted {deleted} comment(s)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GhError as exc:
        print(f"GitHub error: {exc}")
        raise SystemExit(1) from None
