#!/usr/bin/env python3
"""Seed the demo fork with realistic community issues (paced > cooldown).

Each issue triggers a real webhook → the hosted agent answers on GitHub.
Sleeps 35 s between creations so the per-author cooldown (30 s) never blocks.

Usage: .venv/bin/python scripts/devtools/seed_demo.py [--only N]
"""

from __future__ import annotations

import argparse
import os
import sys
import time
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

ISSUES: list[tuple[str, str]] = [
    (
        "Best way to configure a vector index for hybrid search?",
        """Hi! We're building a RAG pipeline on top of FalkorDB and I've been
going through the docs on vector indexes.

I see several knobs (M, efConstruction, efRuntime) and also the full-text
index path, but I couldn't find a single place that explains how to size
them for a moderate corpus (~100k chunks) with hybrid vector + full-text
retrieval.

What would you recommend as a starting point? Does the Python client
expose these settings at index creation time?

Thanks!""",
    ),
    (
        "Where is the query timeout enforced in falkordb-py?",
        """We want to fail fast when a query hangs (long-running Cypher
against a big graph).

Does the Python client enforce a timeout itself, or does everything pass
through to the server? I'd like to point to the exact place in the code
in our internal docs, so a file and symbol reference would help.""",
    ),
    (
        "Feature request: list indexes from the Python client",
        """Small ask: it would help our ops tooling a lot if falkordb-py
exposed a first-class way to list the indexes defined on a graph (vector
+ full-text), similar to what you can inspect in redis-cli / FalkorDB
browser.

Today we resort to raw Cypher calls. Would a helper on the client be
reasonable to add?""",
    ),
]


def _default_repo() -> str:
    slug = (os.environ.get("ARCHIVIST_GH_SLUG") or "").strip()
    if slug:
        return slug
    repos = (os.environ.get("ARCHIVIST_GH_REPOS") or "").strip()
    return repos.split("=", 1)[0].split(",")[0].strip() if repos else ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", type=int, default=0, help="1-based index")
    args = parser.parse_args()
    token = (os.environ.get("ARCHIVIST_GH_TOKEN") or "").strip()
    slug = _default_repo()
    if not (token and slug):
        print("need ARCHIVIST_GH_TOKEN and a repo slug")
        return 1
    gh = GitHub(token)
    picks = [(args.only, ISSUES[args.only - 1])] if args.only else list(
        enumerate(ISSUES, start=1)
    )
    for index, (title, body) in picks:
        issue = gh.create_issue(slug, title, body)
        print(f"created #{issue.get('number')}: {issue.get('html_url')}")
        if index < len(ISSUES):
            time.sleep(35)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GhError as exc:
        print(f"GitHub error: {exc}")
        raise SystemExit(1) from None
