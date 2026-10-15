"""Memory-model tests (M3a) — episodes, bi-temporal facts, recall.

Pins the contract: episode merge idempotency, fact provenance via
``PRODUCED`` and ref anchors via ``ABOUT``, invalidate-don't-delete windows
(valid_at/invalid_at = event time; created_at/expired_at = transaction
time), supersede lineage with tiling windows, and the recall tool's three
views (true now / full history / as-of T). FalkorDB-backed; skips if absent.
"""

from __future__ import annotations

import json

from archivist.agent import Agent
from archivist.ingest import ingest
from archivist.llm import LLMResponse, ToolCall
from archivist.memory import Memory
from archivist.tools import RepoGraph


class ScriptedLLM:
    """Deterministic stand-in: replays canned responses."""

    def __init__(self, script: list[LLMResponse]):
        self.script = list(script)
        self.usage: dict = {}

    def chat(self, messages, tools=None, tool_choice="auto"):
        assert self.script, "script exhausted"
        return self.script.pop(0)


def call(name: str, arguments: dict, call_id: str = "c1") -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[
            ToolCall(
                id=call_id,
                name=name,
                raw_arguments=json.dumps(arguments),
                arguments=arguments,
            )
        ],
    )


def setup(chain_repo, falkordb_or_skip, temp_graph) -> tuple[Memory, RepoGraph]:
    host, port = falkordb_or_skip
    ingest(
        chain_repo, temp_graph, host=host, port=port, rebuild=True, log=lambda m: None
    )
    return (
        Memory(graph=temp_graph, host=host, port=port),
        RepoGraph(graph=temp_graph, host=host, port=port),
    )


def test_episode_merge_is_idempotent_and_updates(
    chain_repo, falkordb_or_skip, temp_graph
):
    mem, rg = setup(chain_repo, falkordb_or_skip, temp_graph)
    eid = mem.record_episode(kind="test-run", source="pytest", id="demo:run-1", total=5)
    assert eid == "demo:run-1"
    mem.record_episode(id=eid, passed=5)  # finishing an existing episode

    assert rg.query("MATCH (e:Episode) RETURN count(*) AS n")["results"][0]["n"] == 1
    row = rg.query(
        "MATCH (e:Episode {id: $id})"
        " RETURN e.kind AS kind, e.source AS source, e.total AS total,"
        " e.passed AS passed, e.created_at AS created_at",
        {"id": eid},
    )["results"][0]
    assert row["kind"] == "test-run"
    assert row["source"] == "pytest"
    assert row["total"] == 5 and row["passed"] == 5
    assert row["created_at"] is not None


def test_fact_provenance_and_refs(chain_repo, falkordb_or_skip, temp_graph):
    mem, rg = setup(chain_repo, falkordb_or_skip, temp_graph)
    eid = mem.record_episode(kind="agent-session", id="s1", question="who calls leaf?")
    out = mem.remember_fact(
        text="leaf is called by helper via h().",
        episode_id=eid,
        refs=[
            {"kind": "symbol", "file": "util.py", "qualname": "leaf"},
            {"kind": "file", "path": "no-such-file.py"},
        ],
    )
    assert out["provenance"] is True
    assert out["refs_attached"] == ["symbol util.py:leaf"]
    assert out["refs_missing"] == ["file no-such-file.py"]

    env = rg.recall("called by helper")
    assert env["status"] == "ok" and env["count"] == 1
    row = env["results"][0]
    assert row["type"] == "fact"
    assert row["episode_id"] == "s1" and row["episode_kind"] == "agent-session"
    assert row["invalid_at"] is None and row["expired_at"] is None

    about = rg.query(
        "MATCH (f:Fact {id: $id})-[:ABOUT]->(x) RETURN x.qualname AS qualname",
        {"id": out["fact_id"]},
    )["results"]
    assert [r["qualname"] for r in about] == ["leaf"]


def test_invalidate_dont_delete_and_as_of_windows(
    chain_repo, falkordb_or_skip, temp_graph
):
    mem, rg = setup(chain_repo, falkordb_or_skip, temp_graph)
    mem.record_episode(kind="agent-session", id="s2")
    mem.remember_fact(
        text="old claim about flask templating",
        episode_id="s2",
        valid_at=1000,
        fact_id="f:demo",
    )

    assert rg.recall("old claim")["count"] == 1  # true now (window open)

    closed = mem.invalidate_fact("f:demo", invalid_at=2000)
    assert closed == {"fact_id": "f:demo", "invalidated": True}
    assert rg.recall("old claim")["status"] == "not_found"  # hidden by default

    row = rg.recall("old claim", include_invalid=True)["results"][0]
    assert (row["valid_at"], row["invalid_at"]) == (1000, 2000)
    assert row["expired_at"] is not None  # when the graph learned it

    assert rg.recall("old claim", as_of=1500)["count"] == 1  # was true mid-window
    assert rg.recall("old claim", as_of=500)["status"] == "not_found"  # before
    assert rg.recall("old claim", as_of=2500)["status"] == "not_found"  # after

    again = mem.invalidate_fact("f:demo", invalid_at=9999)
    assert again["invalidated"] is False and again["reason"] == "already closed"
    kept = rg.recall("old claim", include_invalid=True)["results"][0]
    assert kept["invalid_at"] == 2000  # first close wins — history not rewritten
    assert mem.invalidate_fact("f:ghost")["reason"] == "not found"


def test_supersede_tiles_windows_and_keeps_lineage(
    chain_repo, falkordb_or_skip, temp_graph
):
    mem, rg = setup(chain_repo, falkordb_or_skip, temp_graph)
    mem.record_episode(kind="agent-session", id="s3")
    mem.remember_fact(
        text="helpers is imported by 10 files",
        episode_id="s3",
        valid_at=1000,
        fact_id="f:old",
    )
    result = mem.supersede_fact(
        "f:old",
        text="helpers is imported by 12 files",
        episode_id="s3",
        fact_id="f:new",
    )
    assert result["superseded"] == "f:old" and result["invalidated_old"] is True

    history = {
        r["id"]: r for r in rg.recall("imported by", include_invalid=True)["results"]
    }
    assert history["f:old"]["invalid_at"] is not None
    assert history["f:new"]["valid_at"] == history["f:old"]["invalid_at"]  # tiled

    open_rows = rg.recall("imported by")["results"]
    assert [r["id"] for r in open_rows] == ["f:new"]

    lineage = rg.query(
        "MATCH (n:Fact {id: 'f:new'})-[:SUPERSEDES]->(o:Fact {id: 'f:old'})"
        " RETURN count(*) AS n"
    )["results"]
    assert lineage[0]["n"] == 1


def test_recall_lists_sessions_newest_first_and_matches_answers(
    chain_repo, falkordb_or_skip, temp_graph
):
    mem, rg = setup(chain_repo, falkordb_or_skip, temp_graph)
    mem.record_episode(
        kind="agent-session",
        id="sess:a",
        question="why is tag.py cautious?",
        answer="Legacy bytes handling; see the commit around tag internals.",
        ts=1000,
    )
    mem.record_episode(kind="ingest-run", id="run:1", ts=2000)

    listed = rg.recall(limit=5)
    assert [r["id"] for r in listed["results"]] == ["run:1", "sess:a"]
    assert "sessions" in listed["notes"][0]

    by_answer = rg.recall("legacy bytes")
    assert [r["id"] for r in by_answer["results"]] == ["sess:a"]
    by_question = rg.recall("cautious")
    assert [r["id"] for r in by_question["results"]] == ["sess:a"]
    assert rg.recall("no such memory anywhere")["status"] == "not_found"


def test_content_addressed_fact_ids_converge(chain_repo, falkordb_or_skip, temp_graph):
    mem, rg = setup(chain_repo, falkordb_or_skip, temp_graph)
    mem.record_episode(kind="agent-session", id="s5")
    first = mem.remember_fact(text="leaf is pure", episode_id="s5")
    second = mem.remember_fact(text="leaf  is   pure", episode_id="s5")  # same claim
    assert first["fact_id"] == second["fact_id"]
    assert rg.query("MATCH (f:Fact) RETURN count(*) AS n")["results"][0]["n"] == 1
    produced = rg.query(
        "MATCH (:Episode {id: 's5'})-[:PRODUCED]->(f:Fact) RETURN count(*) AS n"
    )["results"][0]["n"]
    assert produced == 1  # merged, not duplicated
    assert (
        mem.remember_fact(text="leaf is pure", episode_id=None)["fact_id"]
        != first["fact_id"]  # different provenance, different fact
    )


def test_agent_remember_tool_and_memory_gate(chain_repo, falkordb_or_skip, temp_graph):
    mem, rg = setup(chain_repo, falkordb_or_skip, temp_graph)
    mem.record_episode(kind="agent-session", id="s4")

    llm = ScriptedLLM(
        [
            call(
                "remember",
                {
                    "text": "leaf returns 2",
                    "kind": "finding",
                    "refs": [{"kind": "symbol", "file": "util.py", "qualname": "leaf"}],
                },
                "r1",
            ),
            LLMResponse(content="noted"),
        ]
    )
    answer = Agent(rg, llm, memory=mem, episode_id="s4").ask("remember that")
    assert answer.error is None and answer.text == "noted"
    step = answer.steps[0]
    assert step["status"] == "ok" and step["count"] == 1 and step["error"] is None

    facts = rg.recall("leaf returns 2")["results"]
    assert len(facts) == 1 and facts[0]["episode_id"] == "s4"

    # Without a memory writer the tool refuses cleanly (no crash, no write).
    llm2 = ScriptedLLM(
        [call("remember", {"text": "x"}, "r2"), LLMResponse(content="k")]
    )
    answer2 = Agent(rg, llm2).ask("q")
    assert answer2.steps[0]["status"] == "error"
    assert "memory is not enabled" in answer2.steps[0]["error"]

    # Missing text is rejected before touching the graph.
    llm3 = ScriptedLLM([call("remember", {}, "r3"), LLMResponse(content="k")])
    answer3 = Agent(rg, llm3, memory=mem, episode_id="s4").ask("q")
    assert answer3.steps[0]["status"] == "error"
    assert "text is required" in answer3.steps[0]["error"]
