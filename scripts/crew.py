#!/usr/bin/env python3
"""Ask Archivist a question through the three-agent crew (M3b).

Usage:
    python scripts/crew.py "what breaks if url_for changes, and who should review it?"
    python scripts/crew.py "..." --json           # CrewResult, machine-readable
    python scripts/crew.py "..." --no-memory      # do not record episodes/handoffs
    python scripts/crew.py "..." --no-trace       # answers only

The crew runs planner → investigator → verifier (see ``archivist.crew``):
the planner drafts an executable investigation plan, the investigator runs it
against the graph, the verifier re-runs the decisive checks and owns the
final answer. Each phase is recorded as an ``agent-session`` episode and the
handoffs are written as ``(:Episode)-[:HANDOFF]->(:Episode)`` edges, so the
crew's coordination history is queryable in the graph afterwards.

LLM configuration: same environment as scripts/ask.py — a repo-root .env is
loaded; ARCHIVIST_LLM_BASE_URL / ARCHIVIST_LLM_API_KEY / ARCHIVIST_LLM_MODEL
with OPENAI_* fallbacks.
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

from archivist.crew import Crew
from archivist.llm import ChatLLM
from archivist.memory import Memory
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


def render_phase(console, phase: dict, answer, index: int, total: int) -> None:
    from rich.panel import Panel
    from rich.table import Table

    title = f"phase {index}/{total} · {phase['role']} ({phase['status']})"
    console.print(f"[bold cyan]── {title} ──[/bold cyan]")
    console.print(
        Panel(
            answer.text or "",
            border_style="cyan" if phase["status"] == "ok" else "yellow",
        )
    )
    if answer.steps:
        table = Table(show_lines=False, box=None, pad_edge=False)
        table.add_column("#", justify="right")
        table.add_column("tool", style="cyan")
        table.add_column("params")
        table.add_column("status")
        table.add_column("rows", justify="right")
        table.add_column("ms", justify="right")
        for step_index, step in enumerate(answer.steps, start=1):
            status = (
                step["status"]
                if not step["error"]
                else f"error ({step['error'][:40]}…)"
            )
            table.add_row(
                str(step_index),
                step["tool"],
                _short(step["params"]),
                status,
                "-" if step["count"] is None else str(step["count"]),
                str(step["ms"]),
            )
        console.print(table)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question", help="natural-language question about the repo")
    parser.add_argument("--graph", default="repo:flask", help="FalkorDB graph name")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument(
        "--max-steps", type=int, default=8, help="tool rounds per phase"
    )
    parser.add_argument(
        "--no-memory",
        action="store_true",
        help="do not record phases/handoffs as memory episodes",
    )
    parser.add_argument("--json", action="store_true", help="print CrewResult as JSON")
    parser.add_argument("--no-trace", action="store_true", help="hide step tables")
    args = parser.parse_args()

    llm = ChatLLM()
    if not llm.is_configured():
        print(NOT_CONFIGURED, file=sys.stderr)
        return 2

    repo = RepoGraph(graph=args.graph, host=args.host, port=args.port)
    memory = (
        None
        if args.no_memory
        else Memory(graph=args.graph, host=args.host, port=args.port)
    )
    result = Crew(
        repo, llm, memory=memory, max_steps=args.max_steps, source="crew.py"
    ).run(args.question)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
        return 1 if result.error else 0

    from rich.console import Console
    from rich.panel import Panel

    console = Console()
    console.print(f"[bold]Q[/bold] {result.question}\n")
    total = len(result.phases)
    for index, (phase, answer) in enumerate(
        zip(result.phases, result.answers), start=1
    ):
        render_phase(console, phase, answer, index, total)
        handoff = next((h for h in result.handoffs if h["from"] == phase["role"]), None)
        if handoff:
            console.print(
                f"[dim]  ↳ handoff to {handoff['to']}: {_short(handoff['note'], 120)}[/dim]"
            )
        console.print()

    if result.error:
        console.print(
            Panel(f"crew stopped: {result.error}", title="failure", border_style="red")
        )
    else:
        console.print(
            Panel(
                result.text or "",
                title="final answer (verifier-owned)",
                border_style="green",
            )
        )
    trace_ms = sum(q.get("ms", 0) for answer in result.answers for q in answer.queries)
    console.print(
        f"[dim]{result.llm_calls} LLM calls · "
        f"{sum(len(a.queries) for a in result.answers)} queries · {trace_ms} ms · "
        f"phases {len(result.phases)} · handoffs {len(result.handoffs)}[/dim]"
    )
    if result.run_id and memory is not None:
        console.print(f"[dim]memory: run {result.run_id} recorded[/dim]")
    if result.partial:
        console.print(
            "[yellow]note: this run is partial — see phase statuses above.[/yellow]"
        )
    return 1 if result.error else 0


if __name__ == "__main__":
    raise SystemExit(main())
