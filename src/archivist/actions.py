"""Proven actions (inc1): the agent turns graph reasoning into recorded steps.

An action is a ``Fact`` with ``kind="action"`` and an ``action`` JSON
property, so it inherits provenance (``(Episode)-[:PRODUCED]->(Fact)``),
``ABOUT`` anchors, and supersession for free. FalkorDB stores properties as
primitives only, so the ``action`` payload is a JSON string on write and a
parsed object on read.

The closed action set is deliberate: ``review`` (who should sign off),
``change`` (a concrete edit), ``test`` (a regression to add/run). Anything
else is rejected before it touches the graph — a guessed next step is
worse than none.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from falkordb import FalkorDB

from .memory import Memory

ACTIONS = ("review", "change", "test")

_ACTION_KIND = "action"
_STATUS_ORDER = {"proposed": 0, "done": 1, "superseded": 2}


def _valid_ref(ref: Any) -> bool:
    """A ref dict in the shape ``_attach_ref`` accepts (kind + one key)."""
    if not isinstance(ref, dict):
        return False
    kind = str(ref.get("kind") or "").lower()
    if kind == "symbol":
        return bool(ref.get("file") and ref.get("qualname"))
    if kind == "file":
        return bool(ref.get("path"))
    if kind == "commit":
        return bool(ref.get("sha"))
    return False


class Actions:
    """Write + read paths for action facts on one graph (default ``repo:flask``).

    Writes go through the bi-temporal ``Memory`` writer (no second persistence
    path); reads are read-only Cypher over the same ``Fact``/``ABOUT`` graph.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 6379, graph: str = "repo:flask"):
        self.graph_name = graph
        self._db = FalkorDB(host=host, port=port)
        self._graph = self._db.select_graph(graph)
        self.memory = Memory(host=host, port=port, graph=graph)

    def close(self) -> None:
        self._db.close()
        self.memory.close()

    # -- write -------------------------------------------------------------

    def plan_action(
        self,
        summary: str,
        type: str,
        targets: list[dict] | None = None,
        evidence: list[dict] | None = None,
        episode_id: str | None = None,
        refs: list[dict] | tuple[dict, ...] = (),
    ) -> dict:
        """Validate one action, then persist it as an ``action`` Fact.

        ``targets`` are the subjects the action acts on (also anchored via
        ``ABOUT``, since the action is *about* them); ``evidence`` is the
        cited anchors that justify it, stored in the payload. ``refs`` are
        additional anchors. ``type`` must be in :data:`ACTIONS`; a bad type
        raises ``ValueError`` before any write.
        """
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("summary is required")
        if str(type or "").strip().lower() not in ACTIONS:
            raise ValueError(
                f"unknown action type {type!r} (expected one of {', '.join(ACTIONS)})"
            )
        targets = [
            t
            for t in (targets or [])
            if isinstance(t, dict)
        ]
        evidence = [
            e
            for e in (evidence or [])
            if isinstance(e, dict)
        ]
        payload = {
            "type": str(type).strip().lower(),
            "summary": summary.strip(),
            "targets": targets,
            "evidence": evidence,
        }
        anchor_refs = [dict(r) for r in (list(refs) + targets) if _valid_ref(r)]
        result = self.memory.remember_fact(
            text=summary.strip(),
            kind=_ACTION_KIND,
            episode_id=episode_id,
            refs=anchor_refs,
            action=json.dumps(payload),
        )
        self._graph.query(
            "MATCH (f:Fact {id: $id}) SET f.status = 'proposed'",
            {"id": result["fact_id"]},
        )
        return {**result, "action": payload}

    # -- read --------------------------------------------------------------

    def list_actions(self, status: str | None = None) -> dict:
        """All action facts, proposed-first, joined to their ABOUT targets.

        ``status`` filters to ``proposed``/``done``/``superseded`` (``None``
        or ``all`` returns everything). Ordering puts open work first, then
        closed, oldest within each band.
        """
        status_filter = ""
        params: dict = {}
        if status and status != "all":
            status_filter = "WHERE f.status = $status"
            params["status"] = status
        rows = self._graph.query(
            f"""
            MATCH (f:Fact {{kind: $kind}}) {status_filter}
            OPTIONAL MATCH (e:Episode)-[:PRODUCED]->(f)
            OPTIONAL MATCH (f)-[:ABOUT]->(x)
            OPTIONAL MATCH (f)-[:SUPERSEDES]->(old:Fact)
            RETURN f.id AS id, f.text AS text, f.action AS action,
                   f.status AS status, f.valid_at AS valid_at,
                   f.invalid_at AS invalid_at, f.created_at AS created_at,
                   collect(DISTINCT e.id) AS episode_ids,
                   collect(DISTINCT old.id) AS supersedes,
                   labels(x) AS labels, x.path AS path, x.file AS file,
                   x.qualname AS qualname, x.sha AS sha
            ORDER BY f.created_at, f.id
            """,
            {"kind": _ACTION_KIND, **params},
        ).result_set
        return self._shape(rows)

    def _shape(self, rows: list[tuple]) -> dict:
        """Collapse the ABOUT/SUPERSEDES fan-out back into one dict per action."""
        by_id: dict[str, dict] = {}
        for row in rows:
            columns = ["id", "text", "action", "status", "valid_at",
                       "invalid_at", "created_at", "episode_ids", "supersedes",
                       "labels", "path", "file", "qualname", "sha"]
            r = dict(zip(columns, row))
            fid = r["id"]
            item = by_id.get(fid)
            if item is None:
                payload = {}
                raw_action = r["action"]
                if isinstance(raw_action, str):
                    try:
                        payload = json.loads(raw_action)
                    except (ValueError, TypeError):
                        payload = {}
                episodes = [e for e in (r["episode_ids"] or []) if e]
                item = {
                    "id": fid,
                    "summary": r["text"],
                    "type": payload.get("type"),
                    "status": r["status"],
                    "targets": payload.get("targets") or [],
                    "evidence": payload.get("evidence") or [],
                    "episode_id": episodes[0] if episodes else None,
                    "supersedes": [s for s in (r["supersedes"] or []) if s],
                    "created_at": r["created_at"],
                    "refs": [],
                }
                by_id[fid] = item
            labels = r["labels"] or []
            kind = str(labels[0]) if labels else "Node"
            if r.get("path"):
                label = r["path"]
            elif r.get("qualname"):
                label = f"{r.get('file')}:{r['qualname']}"
            elif r.get("sha"):
                label = str(r["sha"])[:10]
            else:
                label = None
            item["refs"].append({"kind": kind, "label": label})
        actions = list(by_id.values())
        actions.sort(
            key=lambda a: (
                _STATUS_ORDER.get(a["status"], 9),
                a["created_at"] or 0,
                a["id"],
            )
        )
        counts = {
            "total": len(actions),
            "proposed": sum(1 for a in actions if a["status"] == "proposed"),
            "done": sum(1 for a in actions if a["status"] == "done"),
            "superseded": sum(1 for a in actions if a["status"] == "superseded"),
        }
        return {"actions": actions, "counts": counts}

    # -- complete ----------------------------------------------------------

    def complete_action(self, action_id: str, status: str = "done") -> dict:
        """Close an action: ``done`` invalidates; ``superseded`` records a
        same-text replacement that supersedes the original (history stays).
        """
        action_id = str(action_id or "").strip()
        if not action_id:
            raise ValueError("action id is required")
        status = str(status or "").strip().lower()
        if status not in ("done", "superseded"):
            raise ValueError("status must be 'done' or 'superseded'")
        current = self._get(action_id)
        if current is None:
            return {"id": action_id, "status": status, "completed": False,
                    "reason": "not found"}
        if status == "done":
            result = self.memory.invalidate_fact(action_id)
            self._graph.query(
                "MATCH (f:Fact {id: $id}) SET f.status = 'done'",
                {"id": action_id},
            )
            return {
                "id": action_id,
                "status": status,
                "completed": result["invalidated"],
                "reason": result.get("reason"),
            }
        # The replacement must carry the same action payload, but a content-
        # addressed id would collide with the original (same summary) and
        # self-supersede. Derive a distinct, deterministic id so re-running
        # the close converges on one replacement instead of a self-loop.
        replacement_id = "fact:" + hashlib.sha256(
            f"supersede:{action_id}".encode()
        ).hexdigest()[:16]
        result = self.memory.supersede_fact(
            action_id,
            text=current["summary"],
            kind=_ACTION_KIND,
            episode_id=current["episode_id"],
            refs=current["raw_refs"],
            fact_id=replacement_id,
            action=json.dumps(current["payload"]),
        )
        if result["supersedes_edge"]:
            self._graph.query(
                "MATCH (f:Fact {id: $id}) SET f.status = 'superseded'",
                {"id": action_id},
            )
            # The replacement is the superseding action: it stays proposed
            # (and carries the SUPERSEDES link back to the closed original).
            self._graph.query(
                "MATCH (f:Fact {id: $id}) SET f.status = 'proposed'",
                {"id": result["fact_id"]},
            )
        return {
            "id": action_id,
            "status": status,
            "completed": result["invalidated_old"],
            "superseded_by": result["fact_id"],
            "supersedes_edge": result["supersedes_edge"],
            "reason": result.get("old_reason"),
        }

    def _get(self, action_id: str) -> dict | None:
        """Raw fields for one action fact, for the supersede path."""
        rows = self._graph.query(
            """
            MATCH (f:Fact {id: $id, kind: $kind})
            OPTIONAL MATCH (e:Episode)-[:PRODUCED]->(f)
            OPTIONAL MATCH (f)-[:ABOUT]->(x)
            RETURN f.text AS text, f.action AS action,
                   collect(DISTINCT e.id) AS episode_ids,
                   labels(x) AS labels, x.path AS path, x.file AS file,
                   x.qualname AS qualname, x.sha AS sha
            """,
            {"id": action_id, "kind": _ACTION_KIND},
        ).result_set
        if not rows:
            return None
        columns = ["text", "action", "episode_ids", "labels", "path",
                   "file", "qualname", "sha"]
        raw_refs: list[dict] = []
        payload = {}
        summary = ""
        episodes: list = []
        for row in rows:
            r = dict(zip(columns, row))
            summary = r["text"]
            raw_action = r["action"]
            if isinstance(raw_action, str):
                try:
                    payload = json.loads(raw_action)
                except (ValueError, TypeError):
                    payload = {}
            episodes = [e for e in (r["episode_ids"] or []) if e]
            labels = r["labels"] or []
            kind = str(labels[0]).lower() if labels else ""
            if kind == "symbol" and r.get("file") and r.get("qualname"):
                raw_refs.append({"kind": "symbol", "file": r["file"], "qualname": r["qualname"]})
            elif kind == "file" and r.get("path"):
                raw_refs.append({"kind": "file", "path": r["path"]})
            elif kind == "commit" and r.get("sha"):
                raw_refs.append({"kind": "commit", "sha": r["sha"]})
        return {
            "summary": summary,
            "payload": payload,
            "episode_id": episodes[0] if episodes else None,
            "raw_refs": raw_refs,
        }
