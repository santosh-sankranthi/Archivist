#!/usr/bin/env python3
"""Print an issue thread (state, labels, comments) for verification.

Usage:
  .venv/bin/python scripts/devtools/check_thread.py --number 1 [--repo slug]
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
    parser.add_argument("--repo", default="")
    parser.add_argument("--chars", type=int, default=600)
    args = parser.parse_args()
    token = (os.environ.get("ARCHIVIST_GH_TOKEN") or "").strip()
    slug = args.repo or _default_repo()
    if not (token and slug):
        print("need ARCHIVIST_GH_TOKEN and a repo slug")
        return 1
    gh = GitHub(token)
    _code, data = gh._request("GET", f"/repos/{slug}/issues/{args.number}")
    print(f"#{data.get('number')} [{data.get('state')}] {data.get('title')}")
    labels = [lab.get("name") for lab in data.get("labels") or []]
    print(f"labels: {labels}")
    print(f"user: {(data.get('user') or {}).get('login')}")
    _code, comments = gh._request(
        "GET", f"/repos/{slug}/issues/{args.number}/comments", params={"per_page": 20}
    )
    for comment in comments or []:
        author = (comment.get("user") or {}).get("login")
        body = (comment.get("body") or "").replace("\n", " ")
        print(f"\n--- comment by {author} ---\n{body[: args.chars]}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GhError as exc:
        print(f"GitHub error: {exc}")
        raise SystemExit(1) from None
