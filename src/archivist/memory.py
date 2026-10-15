"""Agent memory (M3a): episodes + bi-temporal facts, invalidate-don't-delete.

Design mimics Graphiti/Zep patterns without adopting the framework
(M2-RESEARCH §3):

* Everything the agent learns hangs off an ``:Episode`` — a raw unit that
  really happened (an ingest run, an agent session, a test run). Facts
  reference the episode that produced them, so provenance exists by
  construction, not by convention.
* ``:Fact`` nodes carry a **bi-temporal** window. ``valid_at`` /
  ``invalid_at`` are *event time* — when the claim was true in the world.
  ``created_at`` / ``expired_at`` are *transaction time* — when the graph
  learned it and when it learned the claim stopped holding. "True now" =
  open window (``invalid_at IS NULL``); "true at T" filters event time.
* Facts are **never deleted**. A contradiction closes the old fact's window
  and records the new one with a ``SUPERSEDES`` edge; history stays
  queryable, which is the point of memory with a graph under it.
* Writes live here; reads go through ``RepoGraph.recall`` so they share the
  envelope+trace contract and the database-enforced read-only gate.

All ids are caller-overridable and stable (``MERGE`` keyed), so re-running a
script converges instead of duplicating — same rule as ingest.
"""

from __future__ import annotations

import hashlib
import re
import time
from datetime import UTC, datetime
from uuid import uuid4

from falkordb import FalkorDB

_MAX_REFS = 12  # per fact; more likely means a caller bug than a real claim

_PROP_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def stamp_utc(ts: int) -> str:
    """Compact UTC timestamp for generated ids (``20261015T231500Z``)."""
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y%m%dT%H%M%SZ")


def _episode_sets(props: dict) -> tuple[str, dict]:
    """Build a ``SET e.<key> = $p<n>, ...`` clause for episode properties.

    Keys are guard-railed (identifier regex) because they land in the query
    text; values always travel as parameters. Non-primitive values raise —
    memory props are scalar facts, not documents.
    """
    assigns, params = [], {}
    for index, (key, value) in enumerate(sorted(props.items())):
        if not _PROP_NAME.fullmatch(key):
            raise ValueError(f"invalid episode prop name {key!r}")
        if value is not None and not isinstance(value, str | int | float | bool):
            raise ValueError(
                f"episode prop {key!r} must be a primitive, got {type(value).__name__}"
            )
        name = f"p{index}"
        assigns.append(f"e.{key} = ${name}")
        params[name] = value
    return ("SET " + ", ".join(assigns)) if assigns else "", params


class Memory:
    """Write path for agent memory on one graph (default ``repo:flask``).

    Deliberately small and explicit: record an episode, remember a fact,
    invalidate a fact, supersede a fact, link a handoff. Retrieval lives in
    ``RepoGraph.recall``; pruning does not exist (invalidate-don't-delete).
    """

    def __init__(
        self, host: str = "127.0.0.1", port: int = 6379, graph: str = "repo:flask"
    ):
        self.graph_name = graph
        self._graph = FalkorDB(host=host, port=port).select_graph(graph)

    @staticmethod
    def now() -> int:
        return int(time.time())

    # -- episodes ----------------------------------------------------------

    def record_episode(
        self,
        *,
        kind: str = "episode",
        id: str | None = None,
        ts: int | None = None,
        source: str | None = None,
        **props,
    ) -> str:
        """MERGE one episode and set its scalar props; returns the episode id.

        ``ts`` is the event time (defaults to now); ``created_at`` is written
        once, on creation, and never moved — a second call with the same id
        (e.g. finishing an agent session) only updates the given props.
        """
        now = self.now()
        episode_id = id or f"{kind}:{stamp_utc(ts or now)}:{uuid4().hex[:8]}"
        sets, params = _episode_sets(props)
        self._graph.query(
            f"""
            MERGE (e:Episode {{id: $id}})
            ON CREATE SET e.kind = $kind, e.source = $source,
                          e.ts = coalesce($ts, $now), e.created_at = $now
            {sets}
            """,
            {
                "id": episode_id,
                "kind": kind,
                "source": source,
                "ts": ts,
                "now": now,
                **params,
            },
        )
        return episode_id

    def link_handoff(
        self, from_episode: str, to_episode: str, *, note: str | None = None
    ) -> bool:
        """Mark work passing from one session to the next: ``(a)-[:HANDOFF]->(b)``.

        The crew's coordination history is therefore graph state like
        everything else: which agent handed what to whom is queryable after
        the fact, not buried in logs. Idempotent (MERGE); returns False when
        either episode is missing so callers can tell an unwired handoff from
        a recorded one instead of assuming it stuck.
        """
        rows = self._graph.query(
            """
            MATCH (a:Episode {id: $a}), (b:Episode {id: $b})
            MERGE (a)-[h:HANDOFF]->(b)
            SET h.note = $note, h.created_at = $now
            RETURN h.created_at AS created_at
            """,
            {"a": from_episode, "b": to_episode, "note": note, "now": self.now()},
        ).result_set
        return bool(rows)

    # -- facts -------------------------------------------------------------

    def remember_fact(
        self,
        *,
        text: str,
        kind: str = "finding",
        episode_id: str | None = None,
        refs: list[dict] | None = None,
        valid_at: int | None = None,
        fact_id: str | None = None,
    ) -> dict:
        """Record a fact with an open validity window; returns a summary dict.

        ``episode_id`` links provenance ``(Episode)-[:PRODUCED]->(Fact)``
        (dropped, and reported, if the episode does not exist).
        ``refs`` attach ``(Fact)-[:ABOUT]->(target)`` for symbol/file/commit
        anchors; missing targets are reported, never invented.

        Re-remembering the same ``fact_id`` updates text/kind only: the
        validity window is written once — an invalidated fact cannot be
        silently resurrected by a re-run. Without an explicit ``fact_id``
        the id is content-addressed over episode + whitespace-normalized
        text, so remembering the same claim twice converges instead of
        duplicating (same rule as ingest).
        """
        now = self.now()
        if fact_id is None:
            seed = f"{episode_id or ''}|{' '.join(text.split())}"
            fact_id = f"fact:{hashlib.sha256(seed.encode()).hexdigest()[:16]}"
        fid = fact_id
        self._graph.query(
            """
            MERGE (f:Fact {id: $id})
            ON CREATE SET f.created_at = $now, f.valid_at = coalesce($valid_at, $now),
                          f.invalid_at = NULL, f.expired_at = NULL
            SET f.text = $text, f.kind = $kind
            """,
            {"id": fid, "now": now, "valid_at": valid_at, "text": text, "kind": kind},
        )
        provenance = False
        if episode_id:
            rows = self._graph.query(
                """
                MATCH (e:Episode {id: $eid}), (f:Fact {id: $fid})
                MERGE (e)-[:PRODUCED]->(f)
                RETURN f.id AS id
                """,
                {"eid": episode_id, "fid": fid},
            ).result_set
            provenance = bool(rows)
        attached: list[str] = []
        missing: list[str] = []
        for ref in (refs or [])[:_MAX_REFS]:
            resolved = self._attach_ref(fid, ref)
            if resolved is None:
                missing.append(f"unrecognized ref {ref!r}")
            else:
                key, found = resolved
                (attached if found else missing).append(key)
        if refs and len(refs) > _MAX_REFS:
            missing.append(f"{len(refs) - _MAX_REFS} refs beyond the {_MAX_REFS} cap")
        return {
            "fact_id": fid,
            "provenance": provenance,
            "refs_attached": attached,
            "refs_missing": missing,
        }

    def _attach_ref(self, fact_id: str, ref: dict) -> tuple[str, bool] | None:
        """Link one ref dict to the fact; ``None`` = malformed ref dict.

        Returns ``(key, found)`` — a missing target is reported, not
        invented (no stub nodes for typos).
        """
        ref_kind = str(ref.get("kind") or "").lower()
        if ref_kind == "symbol" and ref.get("file") and ref.get("qualname"):
            match = "MATCH (x:Symbol {file: $a, qualname: $b})"
            params = {"a": ref["file"], "b": ref["qualname"]}
            key = f"symbol {ref['file']}:{ref['qualname']}"
        elif ref_kind == "file" and ref.get("path"):
            match = "MATCH (x:File {path: $a})"
            params = {"a": ref["path"]}
            key = f"file {ref['path']}"
        elif ref_kind == "commit" and ref.get("sha"):
            match = "MATCH (x:Commit {sha: $a})"
            params = {"a": ref["sha"]}
            key = f"commit {str(ref['sha'])[:10]}"
        else:
            return None
        rows = self._graph.query(
            f"""
            {match}
            MATCH (f:Fact {{id: $fid}})
            MERGE (f)-[:ABOUT]->(x)
            RETURN f.id AS id
            """,
            {**params, "fid": fact_id},
        ).result_set
        return key, bool(rows)

    def invalidate_fact(self, fact_id: str, *, invalid_at: int | None = None) -> dict:
        """Close a fact's validity window at ``invalid_at`` (default now).

        Idempotent: a fact that is already closed is left untouched so the
        original window survives. Never deletes.
        """
        now = self.now()
        rows = self._graph.query(
            """
            MATCH (f:Fact {id: $id})
            WHERE f.invalid_at IS NULL
            SET f.invalid_at = coalesce($invalid_at, $now), f.expired_at = $now
            RETURN f.id AS id
            """,
            {"id": fact_id, "invalid_at": invalid_at, "now": now},
        ).result_set
        if rows:
            return {"fact_id": fact_id, "invalidated": True}
        exists = self._graph.query(
            "MATCH (f:Fact {id: $id}) RETURN f.id AS id", {"id": fact_id}
        ).result_set
        return {
            "fact_id": fact_id,
            "invalidated": False,
            "reason": "already closed" if exists else "not found",
        }

    def supersede_fact(
        self,
        old_id: str,
        *,
        text: str,
        kind: str = "finding",
        episode_id: str | None = None,
        refs: list[dict] | None = None,
        fact_id: str | None = None,
    ) -> dict:
        """Contradiction handling: close ``old_id``, record the replacement.

        The new fact's ``valid_at`` continues from the old fact's
        ``invalid_at`` so windows tile instead of overlap; a
        ``(:Fact)-[:SUPERSEDES]->(:Fact)`` edge keeps the lineage explicit.
        """
        closed = self.invalidate_fact(old_id)
        rows = self._graph.query(
            "MATCH (f:Fact {id: $id}) RETURN f.invalid_at AS window_end",
            {"id": old_id},
        ).result_set
        start = rows[0][0] if rows and rows[0][0] is not None else None
        result = self.remember_fact(
            text=text,
            kind=kind,
            episode_id=episode_id,
            refs=refs,
            valid_at=start,
            fact_id=fact_id,
        )
        self._graph.query(
            """
            MATCH (n:Fact {id: $new}), (o:Fact {id: $old})
            MERGE (n)-[:SUPERSEDES]->(o)
            """,
            {"new": result["fact_id"], "old": old_id},
        )
        return {
            **result,
            "superseded": old_id,
            "invalidated_old": closed["invalidated"],
        }
