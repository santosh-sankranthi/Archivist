"""Crew tests (M3b) — planner → investigator → verifier, scripted LLMs.

Pins the multi-agent contract: three phases run in order over one shared
graph; handoffs are recorded in the result *and* as HANDOFF edges between
per-phase episodes; role tool scopes are enforced (planner can't run walks,
verifier can't write); a failed phase aborts honestly (partial result, no
silent continuation); investigator facts carry their episode as provenance.
FalkorDB-backed; skips if absent.
"""

from __future__ import annotations

import json

from archivist.agent import TOOL_SCHEMAS
from archivist.crew import PLANNER_TOOLS, VERIFIER_TOOLS, Crew
from archivist.ingest import ingest
from archivist.llm import LLMError, LLMResponse, ToolCall
from archivist.memory import Memory
from archivist.tools import RepoGraph

ALL_TOOLS = {schema["function"]["name"] for schema in TOOL_SCHEMAS}


class ScriptedLLM:
    """Deterministic stand-in: replays canned responses, records requests.

    Script items may be exceptions — raising them exercises the transport
    failure path without a fake server.
    """

    def __init__(self, script: list[LLMResponse]):
        self.script = list(script)
        self.requests: list[dict] = []
        self.usage: dict[str, int] = {}

    def chat(self, messages, tools=None, tool_choice="auto"):
        self.requests.append(
            {"messages": json.loads(json.dumps(messages)), "tools": tools}
        )
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def call(name: str, arguments: dict | None = None, call_id: str = "c1") -> LLMResponse:
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


def text(answer: str) -> LLMResponse:
    return LLMResponse(content=answer)


def setup(chain_repo, falkordb_or_skip, temp_graph) -> tuple[Memory, RepoGraph]:
    host, port = falkordb_or_skip
    ingest(
        chain_repo, temp_graph, host=host, port=port, rebuild=True, log=lambda m: None
    )
    return (
        Memory(graph=temp_graph, host=host, port=port),
        RepoGraph(graph=temp_graph, host=host, port=port),
    )


PLAN = (
    "Objective: who calls helper.\nSteps: 1. callers(helper).\nVerification: rerun it."
)
FINDINGS = "helper() in util.py is called by app.main()."
VERDICT = "Verified: app.main -> helper confirmed; it is the only caller."


def pipeline_script() -> list[LLMResponse]:
    """One canned response sequence covering all three phases in order."""
    return [
        call("overview", {}, "p1"),
        text(PLAN),
        call("find_symbols", {"query": "helper"}, "i1"),
        call("callers", {"symbol": "helper"}, "i2"),
        text(FINDINGS),
        call("callers", {"symbol": "helper", "depth": 1}, "v1"),
        text(VERDICT),
    ]


def _last_user(request: dict) -> str:
    return [m for m in request["messages"] if m["role"] == "user"][-1]["content"]


def _tool_names(schemas) -> set[str]:
    return {schema["function"]["name"] for schema in schemas}


def test_crew_runs_three_phases_and_links_handoffs(
    chain_repo, falkordb_or_skip, temp_graph
):
    mem, rg = setup(chain_repo, falkordb_or_skip, temp_graph)
    llm = ScriptedLLM(pipeline_script())
    result = Crew(rg, llm, memory=mem, run_id="test:crew-1", source="pytest").run(
        "who calls helper?"
    )

    # Phases in order, all clean; the verifier owns the final text.
    assert [p["role"] for p in result.phases] == ["planner", "investigator", "verifier"]
    assert [p["status"] for p in result.phases] == ["ok", "ok", "ok"]
    assert result.text == VERDICT and result.error is None and not result.partial
    assert result.llm_calls == 7  # 2 + 3 + 2 scripted calls

    # Handoffs: result data + graph edges between the phase episodes.
    assert [(h["from"], h["to"]) for h in result.handoffs] == [
        ("planner", "investigator"),
        ("investigator", "verifier"),
    ]
    assert "Objective: who calls helper." in result.handoffs[0]["note"]
    assert "helper()" in result.handoffs[1]["note"]
    assert all(h["linked"] is True for h in result.handoffs)  # edges really written
    edges = rg.query(
        "MATCH (a:Episode)-[h:HANDOFF]->(b:Episode) WHERE a.run = $run"
        " RETURN a.role AS a_role, b.role AS b_role, h.note AS note ORDER BY a.role",
        {"run": "test:crew-1"},
    )["results"]
    assert [(e["a_role"], e["b_role"]) for e in edges] == [
        ("investigator", "verifier"),
        ("planner", "investigator"),
    ]
    assert all(e["note"] for e in edges)

    # Episodes: one per role, kind/source/role set, planner answer = the plan.
    episodes = rg.query(
        "MATCH (e:Episode) WHERE e.run = $run"
        " RETURN e.id AS id, e.kind AS kind, e.role AS role, e.source AS source,"
        " e.answer AS answer ORDER BY e.id",
        {"run": "test:crew-1"},
    )["results"]
    assert [e["role"] for e in episodes] == ["investigator", "planner", "verifier"]
    assert all(
        e["kind"] == "agent-session" and e["source"] == "pytest" for e in episodes
    )
    planner_row = next(e for e in episodes if e["role"] == "planner")
    assert planner_row["answer"] == PLAN

    # Handoff content actually reaches the next phase's model call.
    assert "PLAN from the planner" in _last_user(llm.requests[2])
    assert "INVESTIGATOR'S ANSWER" in _last_user(llm.requests[5])
    assert FINDINGS in _last_user(llm.requests[5])

    # Role tool scopes: planner is orientation-only, verifier may not remember.
    assert _tool_names(llm.requests[0]["tools"]) == set(PLANNER_TOOLS)
    # The crew investigator has no `actions` collaborator (actions are authored
    # in the single-agent loop only), so `act` is omitted from its full scope.
    assert _tool_names(llm.requests[2]["tools"]) == ALL_TOOLS - {"act"}
    verifier_tools = _tool_names(llm.requests[5]["tools"])
    assert verifier_tools == set(VERIFIER_TOOLS) and "remember" not in verifier_tools


def test_crew_without_memory_skips_episodes(chain_repo, falkordb_or_skip, temp_graph):
    _, rg = setup(chain_repo, falkordb_or_skip, temp_graph)
    llm = ScriptedLLM(pipeline_script())
    result = Crew(rg, llm, run_id="test:crew-lite").run("who calls helper?")

    assert result.text == VERDICT
    assert all(p["episode_id"] is None for p in result.phases)
    assert len(result.handoffs) == 2  # still reported as data
    assert all("linked" not in h for h in result.handoffs)  # nothing persisted
    assert rg.query("MATCH (e:Episode) RETURN count(*) AS n")["results"][0]["n"] == 0


def test_role_tool_scopes_are_enforced(chain_repo, falkordb_or_skip, temp_graph):
    mem, rg = setup(chain_repo, falkordb_or_skip, temp_graph)
    llm = ScriptedLLM(
        [
            call("callers", {"symbol": "helper"}, "p1"),  # planner overreach
            text(PLAN),
            text(FINDINGS),
            text(VERDICT),
        ]
    )
    result = Crew(rg, llm, memory=mem, run_id="test:crew-2").run("q")

    assert result.text == VERDICT  # the crew recovered, the attempt is on record
    planner_step = result.answers[0].steps[0]
    assert planner_step["status"] == "error"
    assert "not available" in planner_step["error"]
    assert planner_step["result"]["available"] == sorted(PLANNER_TOOLS)


def test_failed_phase_aborts_crew(chain_repo, falkordb_or_skip, temp_graph):
    mem, rg = setup(chain_repo, falkordb_or_skip, temp_graph)
    llm = ScriptedLLM(
        [call("overview", {}, "p1"), text(PLAN), LLMError("network down")]
    )
    result = Crew(rg, llm, memory=mem, run_id="test:crew-3").run("q")

    assert result.partial is True and result.text == ""
    assert result.error == "investigator: network down"
    assert [p["role"] for p in result.phases] == ["planner", "investigator"]
    assert result.phases[-1]["status"] == "error"
    assert len(result.handoffs) == 1  # planner → investigator only
    assert len(result.answers) == 2
    # The edge into a phase that *started* exists even when that phase failed:
    # the handoff did happen — it is how the investigator got its input.
    edges = rg.query(
        "MATCH (a:Episode)-[h:HANDOFF]->(b:Episode) WHERE a.run = $run"
        " RETURN a.role AS a_role, b.role AS b_role",
        {"run": "test:crew-3"},
    )["results"]
    assert [(e["a_role"], e["b_role"]) for e in edges] == [("planner", "investigator")]
    # Both started episodes exist; the failed one carries its error.
    rows = rg.query(
        "MATCH (e:Episode) WHERE e.run = $run"
        " RETURN e.role AS role, e.error AS error ORDER BY e.id",
        {"run": "test:crew-3"},
    )["results"]
    assert [r["role"] for r in rows] == ["investigator", "planner"]
    failed = next(r for r in rows if r["role"] == "investigator")
    assert failed["error"] == "network down"


def test_investigator_facts_carry_episode_provenance(
    chain_repo, falkordb_or_skip, temp_graph
):
    mem, rg = setup(chain_repo, falkordb_or_skip, temp_graph)
    llm = ScriptedLLM(
        [
            text(PLAN),
            call(
                "remember",
                {
                    "text": "helper is called by main; both live in small files.",
                    "kind": "finding",
                    "refs": [{"kind": "file", "path": "util.py"}],
                },
                "i1",
            ),
            text(FINDINGS),
            call("remember", {"text": "verifier wrote this"}, "v1"),  # must be refused
            text(VERDICT),
        ]
    )
    result = Crew(rg, llm, memory=mem, run_id="test:crew-4").run("q")

    assert result.text == VERDICT
    facts = rg.query("MATCH (f:Fact) RETURN f.id AS id, f.text AS text")["results"]
    assert len(facts) == 1 and "helper is called by main" in facts[0]["text"]
    produced = rg.query(
        "MATCH (e:Episode {id: $eid})-[:PRODUCED]->(f:Fact {id: $fid})"
        " RETURN f.id AS id",
        {"eid": "test:crew-4:investigator", "fid": facts[0]["id"]},
    )["results"]
    assert len(produced) == 1

    verifier_step = next(
        step for step in result.answers[2].steps if step["tool"] == "remember"
    )
    assert verifier_step["status"] == "error"
    assert "not available" in verifier_step["error"]


class _LinkBroken(Memory):
    """link_handoff always fails (returns False) — the loss must surface."""

    def link_handoff(self, *args, **kwargs) -> bool:
        return False


class _BlipMemory(Memory):
    """Every bookkeeping write fails — the crew must degrade, not crash."""

    def record_episode(self, **kwargs):
        raise RuntimeError("simulated graph blip")

    def link_handoff(self, *args, **kwargs):
        raise RuntimeError("simulated graph blip")


def test_link_failure_surfaces_false_on_handoffs(
    chain_repo, falkordb_or_skip, temp_graph
):
    host, port = falkordb_or_skip
    ingest(
        chain_repo, temp_graph, host=host, port=port, rebuild=True, log=lambda m: None
    )
    mem = _LinkBroken(graph=temp_graph, host=host, port=port)
    rg = RepoGraph(graph=temp_graph, host=host, port=port)
    result = Crew(
        rg, ScriptedLLM(pipeline_script()), memory=mem, run_id="test:crew-5"
    ).run("who calls helper?")

    assert result.text == VERDICT and result.error is None
    assert [h["linked"] for h in result.handoffs] == [False, False]
    assert result.notes == []  # a False is a result, not an exception
    count = rg.query("MATCH ()-[h:HANDOFF]->() RETURN count(*) AS n")["results"][0]["n"]
    assert count == 0  # nothing written, and nothing pretends otherwise


def test_bookkeeping_failure_degrades_without_crashing(
    chain_repo, falkordb_or_skip, temp_graph
):
    host, port = falkordb_or_skip
    ingest(
        chain_repo, temp_graph, host=host, port=port, rebuild=True, log=lambda m: None
    )
    mem = _BlipMemory(graph=temp_graph, host=host, port=port)
    rg = RepoGraph(graph=temp_graph, host=host, port=port)
    result = Crew(
        rg, ScriptedLLM(pipeline_script()), memory=mem, run_id="test:crew-6"
    ).run("who calls helper?")

    assert result.text == VERDICT and result.error is None  # the answer survives
    assert any("not recorded" in note for note in result.notes)
    assert [h["linked"] for h in result.handoffs] == [False, False]
    assert result.to_dict()["notes"] == result.notes
