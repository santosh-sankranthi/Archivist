#!/usr/bin/env python3
"""Re-run the issue pipeline for a seeded demo issue, posting for real.

Use when a webhook delivery was missed or an answer flaked: the handler
runs in-process (no webhook) and posts to the existing issue number.

Usage:
  .venv/bin/python scripts/devtools/replay_issue.py --number 1
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))


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

import seed_demo

from archivist.gh import handlers
from archivist.gh.config import load_config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--number", type=int, required=True)
    parser.add_argument("--author", default="judge-demo")
    parser.add_argument("--repo", default="")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    cfg = load_config()
    if not cfg.enabled:
        print("ARCHIVIST_GH_* not configured (repo-root .env)")
        return 1
    slug = args.repo or next(iter(cfg.repos or {}), "")
    graph = cfg.graph_for(slug)
    if not graph:
        print(f"repo {slug!r} not in ARCHIVIST_GH_REPOS")
        return 1
    from archivist.gh.client import GitHub

    gh = GitHub(cfg.token)
    login = gh.get_user().get("login") or ""
    _code, comments = gh._request(
        "GET",
        f"/repos/{slug}/issues/{args.number}/comments",
        params={"per_page": 100},
    )
    answered = any(
        ((comment.get("user") or {}).get("login") or "") == login
        for comment in comments or []
    )
    if answered and not args.force:
        print(f"issue #{args.number} already has a bot reply; --force to re-answer")
        return 0
    title, body = seed_demo.ISSUES[args.number - 1]
    payload = {
        "action": "opened",
        "issue": {
            "number": args.number,
            "title": title,
            "body": body,
            "state": "open",
            "user": {"login": args.author},
            "html_url": f"https://github.com/{slug}/issues/{args.number}",
        },
        "repository": {
            "full_name": slug,
            "html_url": f"https://github.com/{slug}",
        },
    }
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    print(f"replaying issue #{args.number}: {title}")
    handlers.handle_event(cfg, "issues", payload, graph)
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
