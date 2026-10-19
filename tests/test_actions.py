"""Proven-actions tests (inc1) — plan/list/complete over the bi-temporal graph.

Pins the contract: idempotent planning (same content → same ``Fact.id``),
closed action-type rejection, ``ABOUT`` anchor attachment from targets,
``list_actions`` filtering, and ``complete_action`` closing the window (done
= invalidate; superseded = tiled replacement with a ``SUPERSEDES`` edge).
FalkorDB-backed; skips if absent.
"""

from __future__ import annotations

from archivist.actions import ACTIONS, Actions
from archivist.ingest import ingest
from archivist.tools import RepoGraph


def setup(chain_repo, falkordb_or_skip, temp_graph) -> tuple[Actions, RepoGraph]:
    host, port = falkordb_or_skip
    ingest(
        chain_repo, temp_graph, host=host, port=port, rebuild=True, log=lambda m: None
    )
    return (
        Actions(graph=temp_graph, host=host, port=port),
        RepoGraph(graph=temp_graph, host=host, port=port),
    )


def test_plan_action_is_idempotent(chain_repo, falkordb_or_skip, temp_graph):
    acts, rg = setup(chain_repo, falkordb_or_skip, temp_graph)
    first = acts.plan_action(
        "add a regression test for url_for",
        type="test",
        targets=[{"kind": "symbol", "file": "util.py", "qualname": "leaf"}],
        evidence=[{"kind": "file", "path": "util.py"}],
    )
    second = acts.plan_action(
        "add a regression test for url_for",
        type="test",
        targets=[{"kind": "symbol", "file": "util.py", "qualname": "leaf"}],
        evidence=[{"kind": "file", "path": "util.py"}],
    )
    assert first["fact_id"] == second["fact_id"]
    rows = rg.query("MATCH (f:Fact {kind: 'action'}) RETURN count(*) AS n")[
        "results"
    ]
    assert rows[0]["n"] == 1


def test_plan_action_rejects_bad_type(chain_repo, falkordb_or_skip, temp_graph):
    acts, rg = setup(chain_repo, falkordb_or_skip, temp_graph)
    try:
        acts.plan_action("do a thing", type="deploy")
    except ValueError as exc:
        assert "unknown action type" in str(exc)
    else:
        raise AssertionError("expected ValueError for a bad type")
    rows = rg.query("MATCH (f:Fact {kind: 'action'}) RETURN count(*) AS n")[
        "results"
    ]
    assert rows[0]["n"] == 0


def test_plan_action_anchors_targets(chain_repo, falkordb_or_skip, temp_graph):
    acts, rg = setup(chain_repo, falkordb_or_skip, temp_graph)
    out = acts.plan_action(
        "review leaf's callers",
        type="review",
        targets=[{"kind": "symbol", "file": "util.py", "qualname": "leaf"}],
    )
    about = rg.query(
        "MATCH (f:Fact {id: $id})-[:ABOUT]->(x) RETURN x.qualname AS qualname",
        {"id": out["fact_id"]},
    )["results"]
    assert [r["qualname"] for r in about] == ["leaf"]


def test_list_actions_filters_by_status(chain_repo, falkordb_or_skip, temp_graph):
    acts, _rg = setup(chain_repo, falkordb_or_skip, temp_graph)
    acts.plan_action("review a", type="review")
    acts.plan_action("change b", type="change")
    done = acts.plan_action("test c", type="test")
    acts.complete_action(done["fact_id"], status="done")

    all_list = acts.list_actions()
    assert all_list["counts"]["total"] == 3
    assert all_list["counts"]["proposed"] == 2
    assert all_list["counts"]["done"] == 1

    proposed = acts.list_actions(status="proposed")
    assert proposed["counts"]["total"] == 2
    assert all(a["status"] == "proposed" for a in proposed["actions"])

    done_list = acts.list_actions(status="done")
    assert done_list["counts"]["total"] == 1
    assert done_list["actions"][0]["id"] == done["fact_id"]

    # proposed-first ordering: the done action sorts after proposed ones.
    assert [a["status"] for a in all_list["actions"]][:2] == ["proposed", "proposed"]


def test_complete_action_closes_window_and_writes_supersedes(
    chain_repo, falkordb_or_skip, temp_graph
):
    acts, rg = setup(chain_repo, falkordb_or_skip, temp_graph)
    eid = acts.memory.record_episode(kind="agent-session", id="sx")
    out = acts.plan_action(
        "review leaf",
        type="review",
        targets=[{"kind": "symbol", "file": "util.py", "qualname": "leaf"}],
        episode_id=eid,
    )
    fid = out["fact_id"]

    # done closes the window.
    result = acts.complete_action(fid, status="done")
    assert result["completed"] is True
    row = rg.query(
        "MATCH (f:Fact {id: $id}) RETURN f.invalid_at AS invalid_at",
        {"id": fid},
    )["results"][0]
    assert row["invalid_at"] is not None

    # superseded tiles a replacement and writes SUPERSEDES.
    other = acts.plan_action(
        "change helper",
        type="change",
        targets=[{"kind": "symbol", "file": "util.py", "qualname": "helper"}],
        episode_id=eid,
    )
    sup = acts.complete_action(other["fact_id"], status="superseded")
    assert sup["completed"] is True
    assert sup["supersedes_edge"] is True
    new_id = sup["superseded_by"]
    assert new_id != other["fact_id"]  # a supersede never self-loops
    lineage = rg.query(
        "MATCH (n:Fact)-[:SUPERSEDES]->(o:Fact {id: $id}) RETURN n.id AS nid",
        {"id": other["fact_id"]},
    )["results"]
    assert [r["nid"] for r in lineage] == [new_id]
    # the replacement carries the same action payload through, and is the
    # proposed superseder that links back to the closed original.
    listed = {a["id"]: a for a in acts.list_actions()["actions"]}
    assert listed[other["fact_id"]]["status"] == "superseded"
    assert listed[new_id]["type"] == "change"
    assert listed[new_id]["status"] == "proposed"
    assert listed[new_id]["supersedes"] == [other["fact_id"]]


def test_plan_action_stores_payload_and_episode(chain_repo, falkordb_or_skip, temp_graph):
    acts, _rg = setup(chain_repo, falkordb_or_skip, temp_graph)
    eid = acts.memory.record_episode(kind="agent-session", id="se")
    out = acts.plan_action(
        "test url_for",
        type="test",
        targets=[{"kind": "symbol", "file": "util.py", "qualname": "leaf"}],
        evidence=[{"kind": "file", "path": "app.py"}],
        episode_id=eid,
    )
    assert out["action"]["type"] == "test"
    assert out["action"]["targets"][0]["qualname"] == "leaf"
    listed = acts.list_actions()["actions"]
    item = next(a for a in listed if a["id"] == out["fact_id"])
    assert item["type"] == "test"
    assert item["targets"][0]["qualname"] == "leaf"
    assert item["evidence"][0]["path"] == "app.py"
    assert item["episode_id"] == eid


def test_actions_closed_set(chain_repo, falkordb_or_skip, temp_graph):
    assert ACTIONS == ("review", "change", "test")
