#!/usr/bin/env python3
"""M2a demo — run the query tools against the live flask graph.

Regenerates ``docs/demo/m2a-tools.txt``. Read-only; requires the flask
graph to be ingested (see ``scripts/ingest.py``) and FalkorDB reachable
on 127.0.0.1:6379. This is a transcript generator, not the eventual CLI.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))  # runnable without an install

from archivist.tools import RepoGraph


def main() -> None:
    rg = RepoGraph()

    print("== overview ==")
    ov = rg.overview()["results"]
    print(f"repo={ov['repo']['name']} head={ov['repo']['head'][:10]}")
    print("nodes: " + ", ".join(f"{r['label']}={r['count']}" for r in ov["nodes"]))
    print("edges: " + ", ".join(f"{r['type']}={r['count']}" for r in ov["edges"]))

    print()
    print("== find_symbols('url_for') ==")
    for r in rg.find_symbols("url_for")["results"]:
        print(f"  {r['file']}:{r['lineno']}  {r['qualname']} ({r['kind']})")

    print()
    print("== callers('url_for', depth=2, limit=8) — what breaks if it changes ==")
    env = rg.callers("url_for", depth=2, limit=8)
    print(f"  count={env['count']} (weak-guess edges excluded)")
    for r in env["results"]:
        chain = " -> ".join(n["qualname"] for n in r["chain"])
        print(f"  [{r['hops']} hop] {r['file']}: {chain}  sites={r['counts']}")

    print()
    print("== owners('src/flask/app.py') — who should review ==")
    for r in rg.owners("src/flask/app.py", limit=5)["results"]:
        print(f"  {r['name']:<16} commits={r['commits']:<4} last={r['last_ts']}")

    print()
    print("== search_commits('deprecat', limit=5) — archaeology ==")
    for r in rg.search_commits("deprecat", limit=5)["results"]:
        print(f"  {r['sha'][:10]}  {r['summary'][:72]}")

    print()
    print("== file_history('src/flask/blueprints.py', limit=5) ==")
    for r in rg.file_history("src/flask/blueprints.py", limit=5)["results"]:
        print(f"  {r['sha'][:10]}  +{r['added']}/-{r['removed']}  {r['summary'][:66]}")

    total_ms = sum(q.ms for q in rg.trace)
    print()
    print(f"== trace: {len(rg.trace)} queries, {total_ms} ms total ==")


if __name__ == "__main__":
    main()
