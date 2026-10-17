"""Agent-loop tests (M2b) — scripted LLM, real graph, full trace checks.

These pin the loop contract: tool dispatch and ordering, the lean payload fed
back to the model (no query audit trail), error recovery (unknown tools, bad
arguments, tool statuses), the step budget with forced finalization, and
transport failures landing on the Answer. FalkorDB-backed; skips if absent.
"""

from __future__ import annotations

import json
from typing import ClassVar

from archivist.agent import TOOL_SCHEMAS, Agent
from archivist.ingest import ingest
from archivist.llm import LLMError, LLMResponse, ToolCall
from archivist.tools import RepoGraph


class ScriptedLLM:
    """Deterministic stand-in: replays canned responses, records requests."""

    def __init__(self, script: list[LLMResponse]):
        self.script = list(script)
        self.requests: list[dict] = []
        self.usage: dict[str, int] = {}

    def chat(self, messages, tools=None, tool_choice="auto"):
        self.requests.append(
            {"messages": json.loads(json.dumps(messages)), "tools": tools}
        )
        assert self.script, "script exhausted — the loop asked for more calls"
        return self.script.pop(0)


def tool_call(
    name: str, arguments: dict | None = None, call_id: str = "c1"
) -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[
            ToolCall(
                id=call_id,
                name=name,
                raw_arguments=json.dumps(arguments or {}),
                arguments=arguments or {},
            )
        ],
    )


def text_response(text: str) -> LLMResponse:
    return LLMResponse(content=text)


def _repo(chain_repo, falkordb_or_skip, temp_graph) -> RepoGraph:
    host, port = falkordb_or_skip
    ingest(
        chain_repo, temp_graph, host=host, port=port, rebuild=True, log=lambda m: None
    )
    return RepoGraph(graph=temp_graph, host=host, port=port)


def _tool_messages(request: dict) -> list[dict]:
    return [m for m in request["messages"] if m["role"] == "tool"]


def test_loop_happy_path_records_trace(chain_repo, falkordb_or_skip, temp_graph):
    repo = _repo(chain_repo, falkordb_or_skip, temp_graph)
    llm = ScriptedLLM(
        [
            tool_call("overview", {}, call_id="a"),
            tool_call("find_symbols", {"query": "helper"}, call_id="b"),
            text_response("helper lives in util.py; callers come via app.main."),
        ]
    )
    answer = Agent(repo, llm, max_steps=5).ask("where is helper?")

    assert answer.error is None and answer.partial is False
    assert [s["tool"] for s in answer.steps] == ["overview", "find_symbols"]
    assert all(s["status"] == "ok" for s in answer.steps)
    assert answer.text == "helper lives in util.py; callers come via app.main."
    assert answer.llm_calls == 3
    assert answer.queries, "every tool query must land in the trace"
    assert all(
        q["cypher"] and q["tool"] in {"overview", "find_symbols"}
        for q in answer.queries
    )
    assert all(
        q["ms"] >= 0 and q["round"] == 0
        for q in answer.queries
        if q["tool"] == "overview"
    )

    # The model sees compact payloads (no `queries` audit key)...
    second = llm.requests[1]
    payload = json.loads(_tool_messages(second)[0]["content"])
    assert set(payload) == {"tool", "status", "count", "notes", "results"}
    # ...and the exact envelope objects do carry the trail.
    first_env = repo.overview()
    assert first_env["queries"]

    # Assistant tool-call messages use the OpenAI shape.
    assistant = [m for m in second["messages"] if m["role"] == "assistant"][-1]
    assert assistant["tool_calls"][0]["function"]["name"] == "overview"
    # All tool schemas are offered on tool rounds; withheld only on finalize.
    assert len(llm.requests[0]["tools"]) == len(TOOL_SCHEMAS)


def test_unknown_tool_error_is_fed_back(chain_repo, falkordb_or_skip, temp_graph):
    repo = _repo(chain_repo, falkordb_or_skip, temp_graph)
    llm = ScriptedLLM(
        [tool_call("no_such_tool", {}, call_id="a"), text_response("done anyway")]
    )
    answer = Agent(repo, llm).ask("q")

    assert answer.text == "done anyway"
    assert answer.steps[0]["status"] == "error"
    assert "unknown tool" in answer.steps[0]["error"]
    payload = json.loads(_tool_messages(llm.requests[1])[0]["content"])
    assert payload["status"] == "error" and "available" in payload


def test_bad_arguments_json_is_reported(chain_repo, falkordb_or_skip, temp_graph):
    repo = _repo(chain_repo, falkordb_or_skip, temp_graph)
    broken = LLMResponse(
        content=None,
        tool_calls=[
            ToolCall(
                id="x",
                name="find_symbols",
                raw_arguments='{"query": broken}',
                parse_error="Expecting property name enclosed in double quotes",
            )
        ],
    )
    llm = ScriptedLLM([broken, text_response("recovered")])
    answer = Agent(repo, llm).ask("q")

    assert answer.text == "recovered"
    assert answer.steps[0]["status"] == "error"
    assert "not valid JSON" in answer.steps[0]["error"]


def test_tool_status_passthrough(chain_repo, falkordb_or_skip, temp_graph):
    repo = _repo(chain_repo, falkordb_or_skip, temp_graph)
    llm = ScriptedLLM(
        [
            tool_call("find_symbols", {"query": "zzz_none"}, call_id="a"),
            text_response("nothing found"),
        ]
    )
    answer = Agent(repo, llm).ask("q")
    assert answer.steps[0]["status"] == "not_found"
    assert answer.text == "nothing found"


def test_step_budget_forces_final_answer(chain_repo, falkordb_or_skip, temp_graph):
    repo = _repo(chain_repo, falkordb_or_skip, temp_graph)
    llm = ScriptedLLM(
        [
            tool_call("overview", {}, call_id="1"),
            tool_call("overview", {}, call_id="2"),
            text_response("forced final"),
        ]
    )
    answer = Agent(repo, llm, max_steps=2).ask("q")

    assert answer.partial is True
    assert answer.text == "forced final"
    assert len(answer.steps) == 2  # only two tool rounds ran
    last = llm.requests[-1]
    assert last["tools"] is None
    assert any(
        m["role"] == "system" and "Tool budget reached" in m["content"]
        for m in last["messages"]
    )


class ExplodingLLM:
    usage: ClassVar[dict] = {}

    def chat(self, *args, **kwargs):
        raise LLMError("HTTP 401: bad key")


def test_transport_error_lands_on_answer(chain_repo, falkordb_or_skip, temp_graph):
    repo = _repo(chain_repo, falkordb_or_skip, temp_graph)
    answer = Agent(repo, ExplodingLLM()).ask("q")
    assert answer.error and "401" in answer.error
    assert answer.partial is True
    assert answer.text.startswith("(LLM failure:")


def test_tool_crash_feeds_back_and_keeps_trace(
    chain_repo, falkordb_or_skip, temp_graph
):
    repo = _repo(chain_repo, falkordb_or_skip, temp_graph)

    def flaky(**kwargs):
        repo._q("RETURN 1 AS x")  # executed before the crash — must stay audited
        raise RuntimeError("db hiccup")

    repo.callers = flaky  # type: ignore[method-assign]
    llm = ScriptedLLM(
        [tool_call("callers", {"symbol": "helper"}, call_id="a"), text_response("ok")]
    )
    answer = Agent(repo, llm).ask("q")

    step = answer.steps[0]
    assert step["status"] == "error" and "db hiccup" in step["error"]
    assert isinstance(step["ms"], int) and step["ms"] >= 0
    assert any(
        q["tool"] == "callers" and "RETURN 1" in q["cypher"] for q in answer.queries
    ), "queries executed before the crash must survive on the Answer"


def test_empty_model_output_is_flagged(chain_repo, falkordb_or_skip, temp_graph):
    repo = _repo(chain_repo, falkordb_or_skip, temp_graph)
    llm = ScriptedLLM([LLMResponse(content="   ")])
    answer = Agent(repo, llm).ask("q")
    assert answer.partial is True
    assert answer.error == "empty response from model"
    assert answer.text == "(no answer produced)"


def test_negative_max_steps_is_clamped(chain_repo, falkordb_or_skip, temp_graph):
    repo = _repo(chain_repo, falkordb_or_skip, temp_graph)
    llm = ScriptedLLM([text_response("fine")])
    answer = Agent(repo, llm, max_steps=-1).ask("q")
    assert answer.text == "fine"  # not an empty, call-less "success"
    assert len(llm.requests) == 1


def test_large_payloads_are_truncated_for_context(
    chain_repo, falkordb_or_skip, temp_graph
):
    repo = _repo(chain_repo, falkordb_or_skip, temp_graph)
    llm = ScriptedLLM(
        [
            tool_call(
                "query",
                {"cypher": "UNWIND range(1, 60) AS i RETURN i AS n"},
                call_id="a",
            ),
            text_response("done"),
        ]
    )
    answer = Agent(repo, llm).ask("q")
    step = answer.steps[0]
    assert step["count"] == 60
    assert len(step["result"]["results"]) == 40
    assert any("truncated 60 rows to 40" in note for note in step["result"]["notes"])


class StreamingLLM:
    """Scripted responses that also push content through ``on_delta``."""

    def __init__(self, script: list[LLMResponse]):
        self.script = list(script)
        self.calls = 0
        self.usage: dict[str, int] = {}

    def chat(self, messages, tools=None, tool_choice="auto", on_delta=None):
        self.calls += 1
        response = self.script.pop(0)
        content = response.content
        if on_delta is not None and isinstance(content, str) and content:
            half = max(1, len(content) // 2)
            for piece in (content[:half], content[half:]):
                if piece:
                    on_delta(piece)
        return response


def test_ask_emits_step_and_delta_events(chain_repo, falkordb_or_skip, temp_graph):
    repo = _repo(chain_repo, falkordb_or_skip, temp_graph)
    llm = StreamingLLM(
        [
            tool_call("overview", {}, call_id="a"),
            text_response("helper lives in util.py, called from app.main."),
        ]
    )
    events: list[dict] = []
    answer = Agent(repo, llm, max_steps=5).ask(
        "where is helper?", on_event=events.append
    )
    kinds = [event["type"] for event in events]
    assert kinds == ["step_start", "step_result", "answer_delta", "answer_delta"]
    start, result = events[0], events[1]
    assert start == {"type": "step_start", "i": 0, "tool": "overview", "args": {}}
    assert result["i"] == 0 and result["status"] == "ok" and result["ms"] >= 0
    assert "".join(event["text"] for event in events[2:]) == (
        "helper lives in util.py, called from app.main."
    )
    assert answer.text == "helper lives in util.py, called from app.main."
    assert answer.partial is False


def test_ask_cancel_between_steps_stops_the_loop(
    chain_repo, falkordb_or_skip, temp_graph
):
    repo = _repo(chain_repo, falkordb_or_skip, temp_graph)
    flag = {"stop": False}

    class CancellingLLM:
        def __init__(self):
            self.calls = 0
            self.usage: dict[str, int] = {}

        def chat(self, messages, tools=None, tool_choice="auto", on_delta=None):
            self.calls += 1
            flag["stop"] = True  # the abort lands while this call is in flight
            return tool_call("overview", {}, call_id="a")

    llm = CancellingLLM()
    events: list[dict] = []
    answer = Agent(repo, llm, max_steps=5).ask(
        "where is helper?", on_event=events.append, cancel=lambda: flag["stop"]
    )
    assert llm.calls == 1  # the in-flight round completed; nothing new started
    assert events == []  # the tool never ran
    assert answer.steps == []
    assert answer.partial is True
    assert "disconnected" in (answer.error or "")


def test_ask_cancel_before_start_makes_no_calls(
    chain_repo, falkordb_or_skip, temp_graph
):
    repo = _repo(chain_repo, falkordb_or_skip, temp_graph)
    llm = ScriptedLLM([])  # any call would blow up on an empty script
    answer = Agent(repo, llm, max_steps=5).ask("q", cancel=lambda: True)
    assert llm.requests == []
    assert answer.llm_calls == 0
    assert answer.partial is True
