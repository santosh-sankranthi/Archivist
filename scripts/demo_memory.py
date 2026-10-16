#!/usr/bin/env python3
"""M3a demo — persistent memory: a second session recalls the first.

Regenerates ``docs/demo/m3a-memory.txt``. Runs against the live flask graph;
the script first clears its own ``demo:*`` memory nodes so the transcript is
reproducible, then replays a two-session story with a *scripted* model
(call choices and closing lines are canned; every query, count, and result
line below is real, executed against ``repo:flask``).

Story: session one investigates tag.py ownership and *remembers* a finding.
Session two starts fresh and recalls it. Then the finding is superseded —
shown from both the "true now" view and the full-history view, because
memory here is bi-temporal and invalidate-don't-delete.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from archivist.agent import Agent
from archivist.llm import LLMResponse, ToolCall
from archivist.memory import Memory
from archivist.tools import RepoGraph

EP1 = "demo:session:tag-review"
EP2 = "demo:session:tag-recall"
FACT2 = "demo:fact:tag-review:v2"

Q1 = "Who should review changes to src/flask/json/tag.py — and remember the finding."
Q2 = "Did we look into tag.py before? What did we conclude?"

CLOSING1 = (
    "[scripted closing — a real model composes this from the owners result "
    "and records the finding with `remember`; both calls above are real]"
)
CLOSING2 = (
    "[scripted closing — a real model answers from the recall result above, "
    "citing the fact; every row shown here came out of the graph]"
)


class ScriptedLLM:
    """Replays canned responses; the Agent itself keeps the trace."""

    def __init__(self, script):
        self.script = list(script)
        self.usage: dict = {}

    def chat(self, messages, tools=None, tool_choice="auto"):
        return self.script.pop(0)


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
        unit = "row" if step["count"] == 1 else "rows"
        print(
            f"  {step['tool']:<10} -> {status} ({step['count']} {unit}, {step['ms']} ms)"
        )
        for note in step["notes"]:
            print(f"      note: {note}")
    print(f"  [scripted closing] {answer.text}")


def main() -> None:
    print("M3a — persistent memory over repo:flask (scripted model, live graph)")
    print()
    mem = Memory()
    rg = RepoGraph()

    # Reset only this demo's own memory so re-runs converge cleanly: facts
    # first (via PRODUCED provenance — the session-one fact carries a
    # content-addressed id that is not known in advance), then the sessions.
    # Nothing outside this demo's nodes is ever touched.
    mem._graph.query(
        "MATCH (e:Episode)-[:PRODUCED]->(f:Fact) WHERE e.id IN $eps DETACH DELETE f",
        {"eps": [EP1, EP2]},
    )
    mem._graph.query(
        "MATCH (f:Fact {id: $id}) DETACH DELETE f",
        {"id": FACT2},
    )
    mem._graph.query(
        "MATCH (n:Episode) WHERE n.id IN $ids DETACH DELETE n",
        {"ids": [EP1, EP2]},
    )

    print("== setup ==")
    owners = rg.owners("src/flask/json/tag.py", limit=3)["results"]
    top = owners[0]
    print(
        f"owners(src/flask/json/tag.py) -> top: {top['name']} "
        f"({top['commits']} commits, first {top['first_ts']}, last {top['last_ts']})"
    )
    fact_text = (
        f"json/tag.py review routing: {top['name']} has the most commits touching "
        f"src/flask/json/tag.py ({top['commits']})."
    )

    print("\n== session one — investigate, then remember ==")
    ep1 = mem.record_episode(
        kind="agent-session", source="demo_memory.py", id=EP1, question=Q1
    )
    llm1 = ScriptedLLM(
        [
            call("owners", {"path": "src/flask/json/tag.py", "limit": 3}, "a"),
            call(
                "remember",
                {
                    "text": fact_text,
                    "kind": "reviewer",
                    "refs": [{"kind": "file", "path": "src/flask/json/tag.py"}],
                },
                "b",
            ),
            LLMResponse(content=CLOSING1),
        ]
    )
    answer1 = Agent(rg, llm1, memory=mem, episode_id=ep1).ask(Q1)
    show_steps(answer1)
    mem.record_episode(id=ep1, answer=answer1.text, steps=len(answer1.steps))

    print("\n== session two (fresh start) — recall the prior work ==")
    ep2 = mem.record_episode(
        kind="agent-session", source="demo_memory.py", id=EP2, question=Q2
    )
    llm2 = ScriptedLLM(
        [
            call("recall", {"query": "tag.py"}, "c"),
            LLMResponse(content=CLOSING2),
        ]
    )
    answer2 = Agent(rg, llm2, memory=mem, episode_id=ep2).ask(Q2)
    show_steps(answer2)
    mem.record_episode(id=ep2, answer=answer2.text, steps=len(answer2.steps))

    recalled = rg.recall("tag.py")
    print(f"  recall rows ({recalled['count']}, notes: {recalled['notes']}):")
    for row in recalled["results"]:
        if row["type"] == "fact":
            print(f"    [fact] {row['id']}: {row['text']}")
            print(
                f"           valid {row['valid_at']} -> open"
                f" | from episode {row['episode_id']}"
            )
        else:
            print(f"    [session] {row['id']}: {row['question']}")

    print("\n== later — the finding is superseded (invalidate-don't-delete) ==")
    remembered = next(s for s in answer1.steps if s["tool"] == "remember")
    old_fact_id = remembered["result"]["results"]["fact_id"]
    result = mem.supersede_fact(
        old_fact_id,
        text=f"{fact_text} (updated: routing review through the top two authors.)",
        kind="reviewer",
        episode_id=ep2,
        fact_id=FACT2,
        refs=[{"kind": "file", "path": "src/flask/json/tag.py"}],
    )
    print(
        f"  supersede {old_fact_id} -> {FACT2}"
        f" (old invalidated: {result['invalidated_old']})"
    )

    now_view = rg.recall("tag.py", limit=20)
    print(f"\n  true now ({now_view['count']} rows, notes: {now_view['notes']}):")
    for row in now_view["results"]:
        label = row["text"] if row["type"] == "fact" else row["question"]
        print(f"    [{row['type']}] {row['id']}: {label}")

    history = rg.recall("tag.py", include_invalid=True, limit=20)
    print(f"\n  full history ({history['count']} rows, notes: {history['notes']}):")
    for row in history["results"]:
        if row["type"] == "fact":
            window = f"{row['valid_at']} -> {row['invalid_at'] or 'open'}"
            print(f"    [fact] {row['id']} (valid {window}): {row['text']}")
        else:
            print(f"    [session] {row['id']}: {row['question']}")

    print(
        "\n(this demo resets its own nodes on every run; the product never "
        "deletes facts — a contradiction only closes their validity window)"
    )


if __name__ == "__main__":
    main()
