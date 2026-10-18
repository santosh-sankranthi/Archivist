#!/usr/bin/env python3
"""Inject a test issue event into the GitHub adapter (end-to-end harness).

Modes:
  (default)  run the handler IN PROCESS with dry-run on: the full pipeline
             runs (agent, memory, formatting) but nothing is written to
             GitHub — the planned comment is logged instead.
  --post     real run: posts the comment + labels to --number on the repo.
  --http     sign and POST the event to the live server route instead.

Usage:
  .venv/bin/python scripts/devtools/fake_event.py --title "Vector index?" \
      --body "How do I set efRuntime at query time?"
  .venv/bin/python scripts/devtools/fake_event.py --http --title "..."
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import hmac
import json
import logging
import os
import sys
import urllib.request
import uuid
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

from archivist.gh import handlers
from archivist.gh.config import load_config


def _payload(args: argparse.Namespace, slug: str, number: int) -> dict:
    return {
        "action": "opened",
        "issue": {
            "number": number,
            "title": args.title,
            "body": args.body,
            "state": "open",
            "user": {"login": args.author},
            "html_url": f"https://github.com/{slug}/issues/{number}",
        },
        "repository": {
            "full_name": slug,
            "html_url": f"https://github.com/{slug}",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", required=True)
    parser.add_argument("--body", default="")
    parser.add_argument("--repo", default="")
    parser.add_argument("--number", type=int, default=1)
    parser.add_argument("--author", default="judge-demo")
    parser.add_argument("--http", action="store_true")
    parser.add_argument("--post", action="store_true")
    args = parser.parse_args()
    cfg = load_config()
    if not cfg.enabled:
        print("ARCHIVIST_GH_* not configured (repo-root .env)")
        return 1
    slug = args.repo or next(iter(cfg.repos or {}), "")
    graph = cfg.graph_for(slug) if slug else None
    if not graph:
        print(f"repo {slug!r} not in ARCHIVIST_GH_REPOS "
              f"(have: {sorted(cfg.repos or {})})")
        return 1
    payload = _payload(args, slug, args.number)

    if args.http:
        raw = json.dumps(payload).encode("utf-8")
        sig = hmac.new(cfg.secret.encode(), raw, hashlib.sha256).hexdigest()
        req = urllib.request.Request(
            f"{cfg.url}/github/webhook",
            data=raw,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "User-Agent": "archivist-fake-event",
                "X-GitHub-Event": "issues",
                "X-GitHub-Delivery": f"fake-{uuid.uuid4().hex[:12]}",
                "X-Hub-Signature-256": f"sha256={sig}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                print(f"HTTP {resp.status}: {resp.read().decode('utf-8')}")
        except Exception as exc:  # noqa: BLE001
            print(f"request failed: {exc}")
            return 1
        return 0

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    if not args.post:
        cfg = dataclasses.replace(cfg, dry_run=True)
        print("dry-run: nothing will be written to GitHub\n")
    handlers.handle_event(cfg, "issues", payload, graph)
    print("\ndone")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
