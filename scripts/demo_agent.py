#!/usr/bin/env python3
"""M2b demo — the tool-calling loop, end to end, against the live flask graph.

A *scripted* model stands in for a real LLM so this transcript can be
generated without an API key: which calls are made and the closing line are
scripted; the tool calls themselves are real queries against ``repo:flask``
and every result/trace line printed below is real. The script underlines one
moment with real product value: ``callers('Blueprint')`` is **ambiguous**
(two classes after the sansio split), the envelope returns candidates instead
of guessing, and the scripted model retries scoped by file — exactly what
``scripts/ask.py`` will do with a real model once ARCHIVIST_LLM_* is set.

Regenerates ``docs/demo/m2b-agent-loop.txt``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from archivist.agent import Agent
from archivist.llm import LLMResponse, ToolCall
from archivist.tools import RepoGraph

QUESTION = (
    "Who should review changes to src/flask/blueprints.py, and what does "
    "the recent history say?"
)
CLOSING = (
    "[scripted closing for a mechanics demo — a real model composes this "
    "line from the results above once ARCHIVIST_LLM_* is configured]"
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


def main() -> None:
    repo = RepoGraph()
    llm = ScriptedLLM(
        [
            call("owners", {"path": "src/flask/blueprints.py"}, "a"),
            call("file_history", {"path": "src/flask/blueprints.py", "limit": 6}, "b"),
            call("callers", {"symbol": "Blueprint", "depth": 1, "limit": 5}, "c"),
            call(
                "callers",
                {"symbol": "Blueprint", "file": "src/flask/blueprints.py", "limit": 5},
                "d",
            ),
            LLMResponse(content=CLOSING),
        ]
    )
    answer = Agent(repo, llm).ask(QUESTION)

    def step_of(tool: str, status: str | None = None) -> dict:
        for step in answer.steps:
            if step["tool"] == tool and (status is None or step["status"] == status):
                return step
        raise KeyError(f"no step {tool}/{status}")

    print("M2b — tool-calling loop over repo:flask (scripted model, live graph)")
    print()
    print(f"Q: {answer.question}")
    print()
    print("tool calls:")
    for index, step in enumerate(answer.steps, 1):
        params = json.dumps(step["params"], default=str)
        print(
            f"  {index}. {step['tool']}({params}) -> {step['status']}, "
            f"{step['count']} rows, {step['ms']} ms"
        )
    print()

    print("owners -> reviewer candidates ranked by commits touching the file:")
    for row in step_of("owners")["result"]["results"]:
        print(f"  {row['name']:<16} commits={row['commits']}")
    print()

    print("file_history -> newest first (stable order incl. same-second commits):")
    for row in step_of("file_history")["result"]["results"]:
        print(
            f"  {row['sha'][:10]}  +{row['added']}/-{row['removed']}  "
            f"{row['summary'][:56]}"
        )
    print()

    print(
        "callers('Blueprint') -> ambiguous after the sansio split; candidates, no guessing:"
    )
    for cand in step_of("callers", "ambiguous")["result"]["results"]:
        print(f"  {cand['file']}:{cand['lineno']}  {cand['qualname']}")
    print()
    print("callers('Blueprint', file=src/flask/blueprints.py) -> retried, scoped:")
    for row in step_of("callers", "ok")["result"]["results"]:
        chain = " -> ".join(node["qualname"] for node in row["chain"])
        print(f"  [{row['hops']} hop] {row['file']}: {chain}")
    print()

    print("answer:")
    print(f"  {answer.text}")
    print()

    total_ms = sum(query["ms"] for query in answer.queries)
    print(
        f"trace: {len(answer.queries)} queries · {total_ms} ms · "
        f"{answer.llm_calls} LLM calls"
    )
    print()
    print("sample query from the trace (every tool call is auditable like this):")
    sample = answer.queries[0]
    print(f"  [{sample['tool']}] {' '.join(sample['cypher'].split())}")
    print(f"  params={json.dumps(sample['params'])}  ({sample['ms']} ms)")
    print()
    print("note: tool results and trace lines above are real; scripted = which")
    print("calls were made and the closing line. scripts/ask.py drives the same")
    print("loop with a real OpenAI-compatible model (ARCHIVIST_LLM_* env vars).")


if __name__ == "__main__":
    main()
