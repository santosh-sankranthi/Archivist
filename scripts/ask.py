#!/usr/bin/env python3
"""Ask Archivist a question — CLI over the tool-calling agent (M2b).

Usage:
    python scripts/ask.py "who calls url_for?" [--graph repo:flask] [--json]
    python scripts/ask.py "why is tag.py cautious?" --max-steps 6 --no-trace

The answer is rendered with the full audit trail (tools called, Cypher run,
timings) unless --no-trace. --json prints the machine-readable Answer.

LLM configuration comes from the environment; a repo-root .env is loaded:
    ARCHIVIST_LLM_BASE_URL   (or OPENAI_BASE_URL)
    ARCHIVIST_LLM_API_KEY    (or OPENAI_API_KEY)
    ARCHIVIST_LLM_MODEL      (or OPENAI_MODEL)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))  # runnable without an install

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from archivist.agent import Agent
from archivist.llm import ChatLLM
from archivist.tools import RepoGraph

NOT_CONFIGURED = """\
LLM not configured. Set these (environment or repo-root .env):
  ARCHIVIST_LLM_BASE_URL=https://api.openai.com/v1   # or any OpenAI-compatible endpoint
  ARCHIVIST_LLM_API_KEY=sk-...
  ARCHIVIST_LLM_MODEL=gpt-4o-mini
OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL are accepted as fallbacks.
"""


def _short(value: object, width: int = 56) -> str:
    text = json.dumps(value, default=str) if not isinstance(value, str) else value
    text = text.replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"


def render(answer, *, show_trace: bool = True) -> None:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    console = Console()
    console.print(f"[bold]Q[/bold] {answer.question}\n")
    console.print(Panel(answer.text or "", title="answer", border_style="green"))
    if show_trace:
        if answer.steps:
            table = Table(
                title="tool calls", show_lines=False, box=None, pad_edge=False
            )
            table.add_column("#", justify="right")
            table.add_column("tool", style="cyan")
            table.add_column("params")
            table.add_column("status")
            table.add_column("rows", justify="right")
            table.add_column("ms", justify="right")
            for index, step in enumerate(answer.steps, start=1):
                status = (
                    step["status"]
                    if not step["error"]
                    else f"error ({step['error'][:40]}…)"
                )
                table.add_row(
                    str(index),
                    step["tool"],
                    _short(step["params"]),
                    status,
                    "-" if step["count"] is None else str(step["count"]),
                    str(step["ms"]),
                )
            console.print(table)
        if answer.queries:
            total_ms = sum(q.get("ms", 0) for q in answer.queries)
            console.print(
                f"\n[dim]trace: {len(answer.queries)} queries · {total_ms} ms · "
                f"{answer.llm_calls} LLM calls · usage {answer.usage or 'n/a'}[/dim]"
            )
            for query in answer.queries[:12]:
                cypher = " ".join(str(query.get("cypher", "")).split())
                console.print(
                    f"[dim]  [{query.get('tool')}] {_short(cypher, 96)} ({query.get('ms')} ms)[/dim]"
                )
            hidden = len(answer.queries) - 12
            if hidden > 0:
                console.print(f"[dim]  … {hidden} more queries[/dim]")
    if answer.partial:
        console.print(
            "[yellow]note: step budget or LLM failure cut this run short.[/yellow]"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question", help="natural-language question about the repo")
    parser.add_argument("--graph", default="repo:flask", help="FalkorDB graph name")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--json", action="store_true", help="print the Answer as JSON")
    parser.add_argument("--no-trace", action="store_true", help="hide the query trail")
    args = parser.parse_args()

    llm = ChatLLM()
    if not llm.is_configured():
        print(NOT_CONFIGURED, file=sys.stderr)
        return 2

    repo = RepoGraph(graph=args.graph, host=args.host, port=args.port)
    answer = Agent(repo, llm, max_steps=args.max_steps).ask(args.question)

    if args.json:
        print(json.dumps(answer.to_dict(), indent=2, default=str))
    else:
        render(answer, show_trace=not args.no_trace)
    return 1 if answer.error else 0


if __name__ == "__main__":
    raise SystemExit(main())
