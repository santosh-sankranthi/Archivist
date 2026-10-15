#!/usr/bin/env python3
"""M3b demo — the three-agent crew (planner → investigator → verifier).

A *scripted* model stands in for a real LLM so this transcript can be
generated without an API key: the plan text, the investigator's summary and
the verifier's verdict are canned; every tool call, row count, note and trace
line printed below is real, executed against ``repo:flask``. The script
spotlights the M3b beat: the crew's handoffs are *graph state* — each phase
is an ``agent-session`` episode and the chain is linked by
``(:Episode)-[:HANDOFF]->(:Episode)`` edges, queried back from the graph at
the end.

Regenerates ``docs/demo/m3b-handoffs.txt``. Re-runs converge: demo episodes
are prefixed ``demo:crew``, reset at the start, and every write is
MERGE-keyed (same rule as ingest).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from archivist.crew import Crew
from archivist.llm import LLMResponse, ToolCall
from archivist.memory import Memory
from archivist.tools import RepoGraph

RUN = "demo:crew:run-1"
ROLES = ["planner", "investigator", "verifier"]

QUESTION = (
    "What should we know before changing flask.helpers.url_for — who calls "
    "it, and who should review the change?"
)


class ScriptedLLM:
    """Replays canned responses; the Crew/Agent keep the trace."""

    def __init__(self, script):
        self.script = list(script)
        self.usage: dict = {}

    def chat(self, messages, tools=None, tool_choice="auto"):
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def call(name: str, args: dict, cid: str) -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[
            ToolCall(id=cid, name=name, raw_arguments=json.dumps(args), arguments=args)
        ],
    )


def show_steps(answer) -> None:
    for step in answer.steps:
        status = step["status"] if not step["error"] else f"error: {step['error']}"
        if step["count"] is None:
            print(f"  {step['tool']:<10} -> {status} ({step['ms']} ms)")
            continue
        unit = "row" if step["count"] == 1 else "rows"
        print(
            f"  {step['tool']:<10} -> {status} ({step['count']} {unit}, {step['ms']} ms)"
        )
        for note in step["notes"]:
            print(f"      note: {note}")


def main() -> None:
    print("M3b — multi-agent crew over repo:flask (scripted model, live graph)")
    print()
    print("A question goes through planner → investigator → verifier; each phase")
    print("is a recorded session and the handoffs are graph edges — the chain is")
    print("queried back from the graph at the end.")
    print()

    mem = Memory()
    rg = RepoGraph()

    # Reset only this demo's memory so re-runs converge cleanly.
    mem._graph.query(
        "MATCH (e:Episode) WHERE e.id STARTS WITH 'demo:crew' DETACH DELETE e"
    )

    # Setup numbers that the scripted closing lines quote (a real model would
    # read them off the results it just saw; the same deterministic queries
    # run again — live — inside the crew below).
    impact = rg.callers("url_for", depth=2, limit=100)
    direct = rg.callers("url_for", depth=1, limit=100)
    top = rg.owners("src/flask/helpers.py", limit=3)["results"][0]

    plan = (
        "Objective: assess the blast radius of changing flask.helpers.url_for "
        "and the review routing for src/flask/helpers.py.\n"
        "Steps: 1. callers('url_for', depth 2) — impact, weak edges excluded."
        " 2. owners('src/flask/helpers.py') — who has commit authority.\n"
        "Verification: re-run callers depth 1 and re-check the top reviewer."
    )
    findings = (
        "[scripted closing — a real model writes this from the results above: "
        f"url_for fans in from {impact['count']} weak-free callers within 2 hops; "
        f"primary review candidate is {top['name']} "
        f"({top['commits']} commits on src/flask/helpers.py).]"
    )
    verdict = (
        "[scripted closing — the verifier's verdict, composed from its own "
        f"re-runs: confirmed — {direct['count']} direct callers weak-free "
        f"(re-ran depth 1); reviewer re-checked: {top['name']}, "
        f"{top['commits']} commits. Limitation: the resolver sees static call "
        "sites only, so the count is a lower bound.]"
    )

    llm = ScriptedLLM(
        [
            # planner: orientation, then the plan
            call("overview", {}, "p1"),
            LLMResponse(content=plan),
            # investigator: execute the plan
            call("callers", {"symbol": "url_for", "depth": 2, "limit": 100}, "i1"),
            call("owners", {"path": "src/flask/helpers.py", "limit": 3}, "i2"),
            LLMResponse(content=findings),
            # verifier: re-run the decisive checks, then own the answer
            call("callers", {"symbol": "url_for", "depth": 1, "limit": 100}, "v1"),
            call("owners", {"path": "src/flask/helpers.py", "limit": 3}, "v2"),
            LLMResponse(content=verdict),
        ]
    )

    print(f"Q: {QUESTION}")
    print()
    result = Crew(rg, llm, memory=mem, run_id=RUN, source="demo_crew.py").run(QUESTION)

    for index, (phase, answer) in enumerate(zip(result.phases, result.answers), 1):
        print(f"== phase {index}/{len(result.phases)} · {phase['role']} ==")
        show_steps(answer)
        if phase["role"] == "planner":
            print("  [scripted plan]")
            for line in answer.text.splitlines():
                print(f"    {line}")
        elif phase["role"] == "investigator":
            print(f"  [scripted closing] {answer.text}")
        # the verifier's output IS the final answer — printed once, below
        if phase["role"] == "investigator":
            print()
            print("  url_for callers (weak excluded) — first chains:")
            for row in answer.steps[0]["result"]["results"][:4]:
                print(f"    [{row['hops']} hop] {row['file']}: {row['caller']}")
            owners = ", ".join(
                f"{r['name']} ({r['commits']})"
                for r in answer.steps[1]["result"]["results"][:3]
            )
            print(f"  owners(src/flask/helpers.py) -> {owners}")
        if phase["role"] == "verifier":
            print()
            print("  re-check: same depth-1 query, independent run:")
            for row in answer.steps[0]["result"]["results"][:3]:
                print(f"    [{row['hops']} hop] {row['file']}: {row['caller']}")
            owners = answer.steps[1]["result"]["results"][0]
            print(
                f"  reviewer re-confirmed: {owners['name']} "
                f"({owners['commits']} commits, last {owners['last_ts']})"
            )
        handoff = next((h for h in result.handoffs if h["from"] == phase["role"]), None)
        if handoff:
            edge = "edge written" if handoff.get("linked") else "EDGE MISSING"
            print()
            print(f"  handoff → {handoff['to']}: \"{handoff['note']}\" ({edge})")
        print()

    print("== final answer (verifier-owned) ==")
    print(f"  {result.text}")
    print()

    print("== the handoff chain, queried back from the graph ==")
    edges = mem._graph.query(
        "MATCH (a:Episode)-[h:HANDOFF]->(b:Episode) WHERE a.run = $run"
        " RETURN a.role AS a_role, b.role AS b_role, h.note AS note",
        {"run": RUN},
    ).result_set
    edges.sort(key=lambda row: ROLES.index(row[0]))
    for a_role, b_role, note in edges:
        print(f'  {a_role:<13} → {b_role:<13} note: "{note[:72]}…"')
    print()

    print("== episodes recorded for this run ==")
    episodes = mem._graph.query(
        "MATCH (e:Episode) WHERE e.run = $run"
        " RETURN e.id AS id, e.role AS role, e.llm_calls AS llm_calls,"
        " e.steps AS steps, e.queries AS queries",
        {"run": RUN},
    ).result_set
    episodes.sort(key=lambda row: ROLES.index(row[1]))
    for eid, role, llm_calls, steps, queries in episodes:
        print(
            f"  {eid:<32} {role:<13} llm_calls={llm_calls} "
            f"steps={steps} queries={queries}"
        )
    print()
    print(
        "notes: scripted = which calls were made and the closing lines; every\n"
        "query, row count and note above is real (repo:flask). The crew's\n"
        "coordination history is graph state — episodes + HANDOFF edges — so\n"
        '"who worked on this, in what order, passing what" is one query away.'
    )


if __name__ == "__main__":
    main()
