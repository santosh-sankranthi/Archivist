#!/usr/bin/env python3
"""Show recent GitHub webhook deliveries (event, status, timing).

Usage:
  .venv/bin/python scripts/devtools/gh_deliveries.py --hook 699974861 [--limit 8]
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

from archivist.gh.client import GitHub


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--repo", default="")
    parser.add_argument("--hook", type=int, default=0)
    args = parser.parse_args()
    token = os.environ.get("ARCHIVIST_GH_TOKEN", "").strip()
    slug = args.repo or os.environ.get("ARCHIVIST_GH_SLUG", "").strip()
    if not slug:
        repos = os.environ.get("ARCHIVIST_GH_REPOS", "")
        slug = repos.split("=", 1)[0].split(",")[0].strip()
    hook = args.hook or int(os.environ.get("ARCHIVIST_GH_WEBHOOK_ID") or 0)
    if not token or not slug or not hook:
        print("need ARCHIVIST_GH_TOKEN, a repo slug, and a hook id")
        return 1
    gh = GitHub(token)
    _status, data = gh._request(
        "GET", f"/repos/{slug}/hooks/{hook}/deliveries?per_page={args.limit}"
    )
    for item in data or []:
        print(
            item.get("delivered_at"),
            item.get("event"),
            "->",
            item.get("status_code"),
            item.get("guid", "")[:8],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
