#!/usr/bin/env python3
"""List recent GitHub Actions runs/jobs; optionally dump the latest run's logs.

Usage:
  .venv/bin/python scripts/devtools/gh_actions.py [--limit 3] [--log]
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import urllib.request
import zipfile
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

SLUG = "santosh-sankranthi/Archivist"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--log", action="store_true")
    args = parser.parse_args()
    token = os.environ.get("ARCHIVIST_GH_TOKEN", "").strip()
    if not token:
        print("ARCHIVIST_GH_TOKEN not set")
        return 1
    gh = GitHub(token)
    _code, runs = gh._request(
        "GET", f"/repos/{SLUG}/actions/runs?per_page={args.limit}"
    )
    run_list = (runs or {}).get("workflow_runs") or []
    for run in run_list:
        print(
            run["id"],
            run["status"],
            run["conclusion"],
            run["head_sha"][:9],
            run["created_at"],
        )
    if not args.log or not run_list:
        return 0
    run_id = run_list[0]["id"]
    _c, jobs = gh._request("GET", f"/repos/{SLUG}/actions/runs/{run_id}/jobs")
    for job in (jobs or {}).get("jobs") or []:
        print("job", job["id"], job["name"], job["status"], job["conclusion"])
    req = urllib.request.Request(
        f"https://api.github.com/repos/{SLUG}/actions/runs/{run_id}/logs",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "archivist-devtools",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        blob = resp.read()
    archive = zipfile.ZipFile(io.BytesIO(blob))
    for name in archive.namelist():
        text = archive.read(name).decode("utf-8", "replace")
        lines = text.splitlines()
        print(f"---- {name} ({len(lines)} lines) tail ----")
        print("\n".join(lines[-100:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
