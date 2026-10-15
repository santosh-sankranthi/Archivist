#!/usr/bin/env python3
"""CLI: ingest a repository into FalkorDB.

Defaults target this project's dev setup (flask under data/, FalkorDB on
127.0.0.1:6379) so the common case is a zero-argument command.

Usage:
  python scripts/ingest.py [--repo PATH] [--graph NAME] [--rebuild] [--no-prune] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))  # runnable without an install

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:  # dotenv is optional at runtime
    pass

from rich.console import Console
from rich.table import Table

from archivist.graph import GraphWriter
from archivist.ingest import ingest, run_smoke


def _print_summary(console: Console, stats, smoke: dict[str, list[list]]) -> None:
    console.print()
    console.print(f"[bold]Archivist ingest v0[/] — {stats.repo}")
    console.print(
        f"head {stats.head[:12]} · graph '{stats.graph}' · run {stats.run_id} · "
        f"{stats.duration_s}s · {stats.files} files · parse failures {stats.parse_failures}"
    )
    if stats.shallow:
        console.print("[yellow]warning:[/] shallow clone — history is truncated")

    counts = Table(title="Graph counts (total)", title_justify="left")
    counts.add_column("kind")
    counts.add_column("label / type")
    counts.add_column("count", justify="right")
    for label, count in sorted(stats.counts.get("nodes", {}).items()):
        counts.add_row("node", label, str(count))
    for etype, count in sorted(stats.counts.get("edges", {}).items()):
        counts.add_row("edge", etype, str(count))
    console.print(counts)

    console.print("[bold]Cypher smoke tests[/]")
    for name, rows in smoke.items():
        console.print(f"  · {name}")
        if not rows:
            console.print("      (no rows)")
        for row in rows:
            console.print("      " + "  ·  ".join(str(cell) for cell in row))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingest a git repository into FalkorDB."
    )
    parser.add_argument("--repo", type=Path, default=ROOT / "data" / "flask")
    parser.add_argument("--graph", default="repo:flask")
    parser.add_argument("--host", default=os.environ.get("FALKORDB_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("FALKORDB_PORT", "6379"))
    )
    parser.add_argument(
        "--rebuild", action="store_true", help="drop the graph before writing"
    )
    parser.add_argument(
        "--no-prune", action="store_true", help="keep stale nodes from previous runs"
    )
    parser.add_argument(
        "--json", action="store_true", help="print machine-readable stats only"
    )
    args = parser.parse_args(argv)

    console = Console()

    if not (args.repo / ".git").exists():
        console.print(f"[red]error:[/] {args.repo} is not a git repository")
        return 1

    def log(message: str) -> None:
        if not args.json:
            console.print(message)

    # CLI boundary: report failures cleanly and exit non-zero.
    try:
        stats = ingest(
            args.repo,
            args.graph,
            host=args.host,
            port=args.port,
            prune=not args.no_prune,
            rebuild=args.rebuild,
            log=log,
        )
        writer = GraphWriter(host=args.host, port=args.port, graph=args.graph)
        smoke = run_smoke(writer)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]ingest failed:[/] {exc.__class__.__name__}: {exc}")
        return 1

    if args.json:
        print(
            json.dumps(
                {"stats": stats.to_dict(), "smoke": smoke}, indent=2, default=str
            )
        )
    else:
        _print_summary(console, stats, smoke)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
