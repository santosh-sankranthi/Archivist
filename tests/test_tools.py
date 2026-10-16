"""Query-tool tests (M2a) — integration against throwaway graphs.

Pins the tool contract: envelope shape and statuses, exact-over-substring
symbol lookup, ambiguity-as-data, hop depth, weak-edge filtering, and the
aggregations behind owners / history / commit search. Skips without a
reachable FalkorDB.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from archivist.graph import GraphWriter
from archivist.ingest import ingest
from archivist.tools import RepoGraph


def _git(
    repo: Path, *args: str, name: str = "Dev One", email: str = "dev@example.com"
) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
    }
    subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


UTIL = """\
def leaf():
    return 2


def helper():
    return leaf()
"""

APP = """\
from util import helper as h


def main():
    return h()


def unused():
    pass
"""


def _chain_repo(tmp_path: Path) -> Path:
    """Repo with a call chain main -> helper -> leaf and two app.py authors."""
    repo = tmp_path / "chain"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "util.py").write_text(UTIL)
    (repo / "app.py").write_text(APP)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "add app and util")
    # Dev Two makes the next two app.py changes -> most recent owner.
    for body, message in (
        ("\n\ndef extra():\n    return 3\n", "extend app with extra"),
        ("\n\ndef extra():\n    return 4\n", "bump extra"),
    ):
        (repo / "app.py").write_text(APP + body)
        _git(repo, "add", ".", name="Dev Two", email="two@example.com")
        _git(
            repo,
            "commit",
            "-q",
            "-m",
            message,
            name="Dev Two",
            email="two@example.com",
        )
    return repo


def _tools_for(repo: Path, host: str, port: int, graph: str) -> RepoGraph:
    ingest(repo, graph, host=host, port=port, rebuild=True)
    return RepoGraph(graph=graph, host=host, port=port)


def test_overview_envelope(tmp_path: Path, falkordb_or_skip, temp_graph: str):
    host, port = falkordb_or_skip
    tools = _tools_for(_chain_repo(tmp_path), host, port, temp_graph)
    env = tools.overview()
    assert set(env) == {
        "tool",
        "status",
        "params",
        "count",
        "results",
        "notes",
        "queries",
    }
    assert env["status"] == "ok"
    nodes = {row["label"]: row["count"] for row in env["results"]["nodes"]}
    assert nodes["Symbol"] == 5  # leaf helper main unused extra
    assert nodes["Commit"] == 3
    edge_types = {row["type"] for row in env["results"]["edges"]}
    assert {"CALLS", "DEFINES", "MODIFIED"} <= edge_types
    assert env["results"]["repo"]["head"]
    assert env["queries"] and env["queries"][-1]["ms"] >= 0


def test_find_symbols_exact_then_substring(
    tmp_path: Path, falkordb_or_skip, temp_graph
):
    host, port = falkordb_or_skip
    tools = _tools_for(_chain_repo(tmp_path), host, port, temp_graph)
    exact = tools.find_symbols("leaf")
    assert exact["status"] == "ok" and exact["count"] == 1
    assert exact["results"][0]["qualname"] == "leaf"
    assert exact["results"][0]["kind"] == "function"
    near = tools.find_symbols("lea")  # no exact hit -> substring
    assert near["status"] == "ok" and near["results"][0]["name"] == "leaf"
    assert near["notes"] == ["substring matches (no exact name hit)"]
    assert tools.find_symbols("zzz")["status"] == "not_found"
    scoped = tools.find_symbols("helper", file="util.py")
    assert scoped["count"] == 1


def test_ambiguous_names_are_data(tmp_path: Path, falkordb_or_skip, temp_graph):
    host, port = falkordb_or_skip
    repo = tmp_path / "amb"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.py").write_text("def helper():\n    return 1\n")
    (repo / "b.py").write_text("def helper():\n    return 2\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "two helpers")
    tools = _tools_for(repo, host, port, temp_graph)
    env = tools.callers("helper")
    assert env["status"] == "ambiguous"
    assert {row["file"] for row in env["results"]} == {"a.py", "b.py"}
    pinned = tools.callers("helper", file="a.py")  # disambiguated, no callers
    assert pinned["status"] == "ok" and pinned["count"] == 0


def test_callers_depth_chains_and_weak_filter(tmp_path, falkordb_or_skip, temp_graph):
    host, port = falkordb_or_skip
    tools = _tools_for(_chain_repo(tmp_path), host, port, temp_graph)
    direct = tools.callers("leaf")
    assert [(r["caller"], r["hops"]) for r in direct["results"]] == [("helper", 1)]
    assert direct["results"][0]["chain"] == [
        {"file": "util.py", "qualname": "helper"},
        {"file": "util.py", "qualname": "leaf"},
    ]
    deep = tools.callers("leaf", depth=2)
    assert [(r["caller"], r["hops"]) for r in deep["results"]] == [
        ("helper", 1),
        ("main", 2),
    ]
    main_row = deep["results"][1]
    assert [n["qualname"] for n in main_row["chain"]] == ["main", "helper", "leaf"]
    assert main_row["counts"] == [1, 1]
    assert main_row["weaks"] == [False, False]

    # The limit cut is disclosed, not silent (REVIEW-M4R Gate C O1).
    capped = tools.callers("leaf", depth=2, limit=1)
    assert [(r["caller"], r["hops"]) for r in capped["results"]] == [("helper", 1)]
    assert any(
        "showing 1 of 2 distinct callers" in note for note in capped["notes"]
    ), "limit truncation must be surfaced in notes"

    # Inject a weak (guessed) edge main -> leaf: excluded by default...
    writer = GraphWriter(host=host, port=port, graph=temp_graph)
    writer.smoke(
        "MATCH (a:Symbol {file: 'app.py', qualname: 'main'}), "
        "(b:Symbol {file: 'util.py', qualname: 'leaf'}) "
        "MERGE (a)-[r:CALLS]->(b) SET r.count = 1, r.lines = [9], r.weak = true"
    )
    # ...plus a weak-only caller (unused) that the default view must flag
    # as hidden rather than letting it vanish silently.
    writer.smoke(
        "MATCH (a:Symbol {file: 'app.py', qualname: 'unused'}), "
        "(b:Symbol {file: 'util.py', qualname: 'leaf'}) "
        "MERGE (a)-[r:CALLS]->(b) SET r.count = 1, r.lines = [13], r.weak = true"
    )
    default = tools.callers("leaf", depth=2)
    assert [(r["caller"], r["hops"]) for r in default["results"]] == [
        ("helper", 1),
        ("main", 2),
    ]
    assert any(
        "1 caller(s) hidden by the weak-edge filter" in note
        for note in default["notes"]
    ), "hidden weak-only callers must be surfaced in notes"
    # ...and visible (flagged, shortest path) when explicitly requested.
    with_weak = tools.callers("leaf", depth=2, include_weak=True)
    by_caller = {r["caller"]: r for r in with_weak["results"]}
    assert by_caller["main"]["hops"] == 1
    assert by_caller["main"]["weaks"] == [True]


def test_callees_chain(tmp_path: Path, falkordb_or_skip, temp_graph):
    host, port = falkordb_or_skip
    tools = _tools_for(_chain_repo(tmp_path), host, port, temp_graph)
    one_hop = tools.callees("main")
    assert [(r["callee"], r["hops"]) for r in one_hop["results"]] == [("helper", 1)]
    deep = tools.callees("main", depth=2)
    assert [(r["callee"], r["hops"]) for r in deep["results"]] == [
        ("helper", 1),
        ("leaf", 2),
    ]
    leaf_row = deep["results"][1]
    assert [n["qualname"] for n in leaf_row["chain"]] == ["main", "helper", "leaf"]
    assert deep["notes"] == ["source = main"]

    capped = tools.callees("main", depth=2, limit=1)
    assert [(r["callee"], r["hops"]) for r in capped["results"]] == [("helper", 1)]
    assert any(
        "showing 1 of 2 distinct callees" in note for note in capped["notes"]
    ), "limit truncation must be surfaced in notes"


def test_importers_history_owners_commits(tmp_path: Path, falkordb_or_skip, temp_graph):
    host, port = falkordb_or_skip
    tools = _tools_for(_chain_repo(tmp_path), host, port, temp_graph)

    imports = tools.importers("util")
    assert [(r["file"], r["line"]) for r in imports["results"]] == [("app.py", 1)]
    assert tools.importers("nowhere")["status"] == "not_found"

    history = tools.file_history("app.py")
    assert history["count"] == 3
    assert [r["summary"] for r in history["results"]] == [
        "bump extra",
        "extend app with extra",
        "add app and util",
    ]
    assert all(r["author"] for r in history["results"])
    assert tools.file_history("nope.py")["status"] == "not_found"

    owners = tools.owners("app.py")
    assert [(r["name"], r["commits"]) for r in owners["results"]] == [
        ("Dev Two", 2),
        ("Dev One", 1),
    ]
    assert owners["results"][0]["last_ts"] >= owners["results"][0]["first_ts"]

    found = tools.search_commits("EXTEND")  # case-insensitive
    assert found["count"] == 1
    assert found["results"][0]["summary"] == "extend app with extra"


def test_trace_and_raw_query(tmp_path: Path, falkordb_or_skip, temp_graph):
    host, port = falkordb_or_skip
    tools = _tools_for(_chain_repo(tmp_path), host, port, temp_graph)
    tools.find_symbols("leaf")
    tools.callers("leaf")
    assert len(tools.trace) >= 3  # resolve + walk at minimum, in order
    assert all(record.cypher and record.ms >= 0 for record in tools.trace)
    env = tools.query("MATCH (s:Symbol) RETURN count(s) AS n")
    assert env["results"][0]["n"] == 5
    assert env["queries"][0]["cypher"].startswith("MATCH (s:Symbol)")


def test_argument_guards_and_query_clamp(tmp_path: Path, falkordb_or_skip, temp_graph):
    host, port = falkordb_or_skip
    tools = _tools_for(_chain_repo(tmp_path), host, port, temp_graph)
    with pytest.raises(ValueError, match="integer"):
        tools.callers("leaf", depth="two")
    with pytest.raises(ValueError, match="integer"):
        tools.find_symbols("leaf", limit=None)
    env = tools.query("UNWIND range(1, 150) AS i RETURN i AS n", limit=1000)
    assert len(env["results"]) == 100  # documented 1-100 cap applies everywhere
