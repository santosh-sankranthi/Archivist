"""FalkorDB write layer for ingest v0.

Every phase is an idempotent, UNWIND-batched MERGE over rows, keyed on stable
identities (paths, shas, emails, file+qualname) — re-ingest converges instead
of duplicating. A per-run stamp (``run_id``) lets us prune nodes that no
longer appear in the repo (or its history).
"""

from __future__ import annotations

from collections.abc import Iterable

from falkordb import FalkorDB

_BATCH = 500

# Lookup indexes the read tools rely on. M2-RESEARCH measured that repo:flask
# shipped with none, while name/path/sha lookups are the hot path of every
# tool; ingest ensures them after each run (idempotent, creates are async).
INDEXES: list[tuple[str, str]] = [
    ("range Symbol.name", "CREATE INDEX FOR (s:Symbol) ON (s.name)"),
    ("range File.path", "CREATE INDEX FOR (f:File) ON (f.path)"),
    ("range Commit.sha", "CREATE INDEX FOR (c:Commit) ON (c.sha)"),
    ("range Module.name", "CREATE INDEX FOR (m:Module) ON (m.name)"),
    ("fulltext Symbol.name", "CREATE FULLTEXT INDEX FOR (s:Symbol) ON (s.name)"),
]


class GraphWriter:
    """Thin wrapper around one FalkorDB graph with ingest-specific writes."""

    def __init__(
        self, host: str = "127.0.0.1", port: int = 6379, graph: str = "repo:flask"
    ):
        self.graph_name = graph
        self._db = FalkorDB(host=host, port=port)
        self.graph = self._db.select_graph(graph)

    # -- low level ----------------------------------------------------------

    def _run(self, query: str, params: dict | None = None) -> None:
        self.graph.query(query, params or {})

    def _batched(
        self, query: str, rows: Iterable[dict], extra: dict | None = None
    ) -> None:
        """Execute ``query`` once per chunk of ``rows`` (UNWIND $rows AS row)."""
        rows = list(rows)
        for start in range(0, len(rows), _BATCH):
            self.graph.query(
                query, {"rows": rows[start : start + _BATCH], **(extra or {})}
            )

    def delete(self) -> None:
        """Drop the whole graph. Tolerates an absent graph."""
        try:
            self.graph.delete()
        except Exception:
            if self.graph_name in self._db.list_graphs():
                raise

    def stats(self) -> dict[str, dict[str, int]]:
        nodes = self.graph.query(
            "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS cnt ORDER BY label"
        ).result_set
        edges = self.graph.query(
            "MATCH ()-[e]->() RETURN type(e) AS type, count(*) AS cnt ORDER BY type"
        ).result_set
        return {
            "nodes": {row[0]: row[1] for row in nodes},
            "edges": {row[0]: row[1] for row in edges},
        }

    def smoke(self, query: str) -> list[list]:
        return self.graph.query(query).result_set

    def ensure_indexes(self) -> dict[str, list[str]]:
        """Create the lookup indexes the read tools rely on (idempotent).

        FalkorDB rejects re-creating an existing index with an "already
        indexed" error — on re-runs that *is* the success signal.
        """
        created: list[str] = []
        existing: list[str] = []
        for name, cypher in INDEXES:
            try:
                self._run(cypher)
                created.append(name)
            except Exception as exc:
                if "already indexed" in str(exc).lower():
                    existing.append(name)
                else:
                    raise
        return {"created": created, "existing": existing}

    def explain(self, query: str) -> list[str]:
        """Execution plan for a read query (dev checks / docs)."""
        plan = self._db.execute_command("GRAPH.EXPLAIN", self.graph_name, query)
        return [str(line) for line in plan]

    # -- write phases (order matters: commits before HEAD edge, files before
    #    symbols/modifications so stubs are only created for history-only
    #    paths, symbols before CALLS edges) ----------------------------------

    def write_repo(
        self, *, name: str, path: str, head: str, updated_at: int, run_id: str
    ) -> None:
        self._run(
            """
            MERGE (r:Repo {name: $name})
            SET r.path = $path, r.head = $head, r.updated_at = $updated_at, r.run_id = $run_id
            WITH r
            MERGE (c:Commit {sha: $head})
            MERGE (r)-[:HEAD]->(c)
            """,
            {
                "name": name,
                "path": path,
                "head": head,
                "updated_at": updated_at,
                "run_id": run_id,
            },
        )

    def write_files(self, rows: Iterable[dict], run_id: str) -> None:
        self._batched(
            """
            UNWIND $rows AS row
            MERGE (f:File {path: row.path})
            SET f.lang = row.lang, f.loc = row.loc, f.run_id = $run_id
            """,
            rows,
            {"run_id": run_id},
        )

    def write_authors(self, rows: Iterable[dict]) -> None:
        self._batched(
            """
            UNWIND $rows AS row
            MERGE (a:Author {email: row.email})
            SET a.name = row.name, a.commits_count = row.commits_count
            """,
            rows,
        )

    def write_commits(self, rows: Iterable[dict], run_id: str) -> None:
        self._batched(
            """
            UNWIND $rows AS row
            MERGE (c:Commit {sha: row.sha})
            SET c.author_name = row.author_name, c.ts = row.ts,
                c.seq = row.seq, c.summary = row.summary,
                c.message = row.message, c.run_id = $run_id
            MERGE (a:Author {email: row.author_email})
            SET a.name = row.author_name
            MERGE (a)-[:AUTHORED]->(c)
            """,
            rows,
            {"run_id": run_id},
        )

    def write_parents(self, rows: Iterable[dict]) -> None:
        self._batched(
            """
            UNWIND $rows AS row
            MERGE (c:Commit {sha: row.sha})
            MERGE (p:Commit {sha: row.parent})
            MERGE (c)-[:PARENT]->(p)
            """,
            rows,
        )

    def write_modifications(self, rows: Iterable[dict], run_id: str) -> None:
        self._batched(
            """
            UNWIND $rows AS row
            MATCH (c:Commit {sha: row.sha})
            MERGE (f:File {path: row.path})
            SET f.run_id = $run_id
            MERGE (c)-[m:MODIFIED]->(f)
            SET m.added = row.added, m.removed = row.removed
            """,
            rows,
            {"run_id": run_id},
        )

    def write_symbols(self, rows: Iterable[dict], run_id: str) -> None:
        self._batched(
            """
            UNWIND $rows AS row
            MERGE (f:File {path: row.path})
            SET f.run_id = $run_id
            MERGE (s:Symbol {file: row.path, qualname: row.qualname})
            SET s.name = row.name, s.kind = row.kind, s.lineno = row.lineno,
                s.run_id = $run_id
            MERGE (f)-[:DEFINES]->(s)
            """,
            rows,
            {"run_id": run_id},
        )

    def write_imports(self, rows: Iterable[dict], run_id: str) -> None:
        self._batched(
            """
            UNWIND $rows AS row
            MERGE (f:File {path: row.path})
            SET f.run_id = $run_id
            MERGE (m:Module {name: row.module})
            SET m.run_id = $run_id
            MERGE (f)-[i:IMPORTS {line: row.line}]->(m)
            """,
            rows,
            {"run_id": run_id},
        )

    def write_calls(self, rows: Iterable[dict], run_id: str) -> None:
        """CALLS edges between Symbols, aggregated per caller->callee pair.

        MATCH (not MERGE) on the endpoints on purpose: resolution only ever
        links symbols that exist, so a miss means an upstream bug — better
        silently-dropped rows than accidental stub nodes. ``c.lines`` keeps
        the call sites, ``count`` = len(lines), ``weak`` flags resolver
        guesses (rings 3-4) so queries can down-rank or filter them.
        """
        self._batched(
            """
            UNWIND $rows AS row
            MATCH (s:Symbol {file: row.caller_file, qualname: row.caller_qual})
            MATCH (t:Symbol {file: row.callee_file, qualname: row.callee_qual})
            MERGE (s)-[c:CALLS]->(t)
            SET c.count = row.count, c.lines = row.lines, c.weak = row.weak,
                c.run_id = $run_id
            """,
            rows,
            {"run_id": run_id},
        )

    def prune(self, run_id: str) -> None:
        """Delete File/Symbol/Module nodes not produced by the current run.

        Commit and Author nodes are append-only: git history never shrinks on
        a normal re-ingest, so they carry no run stamp.
        """
        for label in ("File", "Symbol", "Module"):
            self._run(
                f"MATCH (n:{label}) WHERE n.run_id IS NULL OR n.run_id <> $run_id DELETE n",
                {"run_id": run_id},
            )
        # Edges between surviving symbols can go stale when a call site is
        # removed (both endpoints still exist) — stamp-prune them as well.
        self._run(
            "MATCH ()-[c:CALLS]->() WHERE c.run_id IS NULL OR c.run_id <> $run_id DELETE c",
            {"run_id": run_id},
        )
