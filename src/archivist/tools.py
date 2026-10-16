"""Graph query tools (M2) — the read side the agent reasons over.

Design contract (see DECISIONS.md D-010):

* Read-only tools over one ingested repo graph; ingest owns the write path.
* Every tool returns an *envelope*: ``{"tool", "status", "params", "results",
  "count", "notes", "queries"}`` with ``status`` in ``{"ok", "ambiguous",
  "not_found"}``. Ambiguity is data, not an exception — when a name matches
  several symbols the caller gets candidates back to disambiguate; tools
  never guess which one you meant.
* Envelopes carry the exact Cypher, params, and duration of every query they
  ran. Traceability is a product feature (visible reasoning paths), so the
  audit trail travels with the data from day one.
* CALLS edges resolved by name-guessing carry ``weak=True`` (rings 3–4 of
  ``archivist.calls``). Tools exclude them by default — honest numbers over
  impressive ones — unless the caller opts in with ``include_weak=True``.
  When guesses are hidden, ``notes`` says how many were filtered, so a zero
  is never silent about what was excluded.
* Rows are JSON-able plain structures; no FalkorDB client objects leak out,
  so envelopes can go straight into prompts, CLI rendering, or the UI.
* Every query runs through ``GRAPH.RO_QUERY`` — the read path is enforced
  read-only by the database itself, not by convention (M2-RESEARCH).
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any

from falkordb import FalkorDB

MAX_DEPTH = 5  # deeper walks explode combinatorially on real repos
MAX_LIMIT = 100

SymbolId = tuple[str, str]  # (file, qualname) — the graph's symbol identity


def _clamp_depth(depth: int) -> int:
    try:
        value = int(depth)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"depth must be an integer, got {depth!r}") from exc
    return max(1, min(value, MAX_DEPTH))


def _clamp_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"limit must be an integer, got {limit!r}") from exc
    return max(1, min(value, MAX_LIMIT))


@dataclass
class QueryRecord:
    """One executed Cypher statement, kept so answers can show their work."""

    cypher: str
    params: dict
    ms: int
    rows: int


class RepoGraph:
    """Read-only query tools over one repo graph (default ``repo:flask``)."""

    def __init__(
        self, graph: str = "repo:flask", host: str = "127.0.0.1", port: int = 6379
    ):
        self.graph_name = graph
        self._db = FalkorDB(host=host, port=port)
        self._graph = self._db.select_graph(graph)
        self.trace: list[QueryRecord] = []

    def close(self) -> None:
        """Release this instance's client connection (best-effort).

        Short-lived owners (the web layer, tests) close explicitly and get
        flat fd counts; the long-running CLIs simply exit.
        """
        self._db.close()

    # -- plumbing ----------------------------------------------------------

    def _mark(self) -> int:
        return len(self.trace)

    def _q(self, cypher: str, params: dict | None = None) -> list[dict]:
        """Run one read query, record it in the trace, return row dicts."""
        t0 = time.perf_counter()
        result = self._graph.ro_query(cypher, params or {})
        columns = [header[1] for header in result.header]
        rows = [dict(zip(columns, row, strict=True)) for row in result.result_set]
        self.trace.append(
            QueryRecord(
                cypher=cypher.strip(),
                params=params or {},
                ms=round((time.perf_counter() - t0) * 1000),
                rows=len(rows),
            )
        )
        return rows

    def _envelope(
        self,
        tool: str,
        status: str,
        params: dict,
        results: Any,
        notes: list[str] | None = None,
        since: int = 0,
    ) -> dict:
        count = len(results) if isinstance(results, list) else None
        return {
            "tool": tool,
            "status": status,
            "params": params,
            "count": count,
            "results": results,
            "notes": list(notes or []),
            "queries": [asdict(record) for record in self.trace[since:]],
        }

    def _resolve(
        self,
        tool: str,
        name: str,
        file: str | None,
        since: int,
        params: dict,
        limit: int = 25,
    ) -> tuple[dict | None, dict | None]:
        """Resolve a symbol reference to exactly one symbol, or explain why not.

        Exact qualname hits shadow plain name matches; anything else is
        ambiguous and comes back as candidates. Returns ``(symbol, None)`` on
        success or ``(None, envelope)`` — no silent guessing.
        """
        scope = " AND s.file = $file" if file else ""
        rows = self._q(
            f"""
            MATCH (s:Symbol)
            WHERE (s.qualname = $name OR s.name = $name){scope}
            RETURN s.file AS file, s.qualname AS qualname, s.name AS name,
                   s.kind AS kind, s.lineno AS lineno, s.qualname = $name AS qual_hit
            ORDER BY qual_hit DESC, file, qualname
            LIMIT $limit
            """,
            {"name": name, "limit": limit, **({"file": file} if file else {})},
        )
        exact = [row for row in rows if row["qual_hit"]]
        candidates = exact or rows
        if not candidates:
            return None, self._envelope(
                tool,
                "not_found",
                params,
                [],
                [f"no Symbol named {name!r}" + (f" in {file}" if file else "")],
                since,
            )
        if len(candidates) > 1:
            return None, self._envelope(
                tool,
                "ambiguous",
                params,
                candidates,
                ["several symbols match — pass file/qualname to disambiguate"],
                since,
            )
        return candidates[0], None

    def _hidden_weak_note(
        self,
        kind: str,
        direction: str,
        target: dict,
        depth: int,
        cap: int,
        shown: dict,
    ) -> list[str]:
        """Note peers visible only when weak guesses are included.

        The default view drops weak edges; without this reminder a count of 0
        would look identical to "nothing calls this". One extra read query
        keeps the honest-numbers promise visible in the envelope itself.
        """
        if direction == "in":  # who calls the target
            pattern = (
                f"(c:Symbol)-[:CALLS*1..{depth}]->"
                "(t:Symbol {file: $file, qualname: $qualname})"
            )
        else:  # what the target calls
            pattern = (
                "(s:Symbol {file: $file, qualname: $qualname})-"
                f"[:CALLS*1..{depth}]->(c:Symbol)"
            )
        rows = self._q(
            f"""
            MATCH p={pattern}
            WHERE (c.file <> $file OR c.qualname <> $qualname)
              AND any(r IN relationships(p) WHERE coalesce(r.weak, false))
            RETURN DISTINCT c.file AS file, c.qualname AS peer
            LIMIT {cap}
            """,
            {"file": target["file"], "qualname": target["qualname"]},
        )
        hidden = {(row["file"], row["peer"]) for row in rows} - set(shown)
        if not hidden:
            return []
        message = (
            f"{len(hidden)} {kind}(s) hidden by the weak-edge filter"
            " — pass include_weak=true to include"
        )
        return [message]

    # -- tools -------------------------------------------------------------

    def overview(self) -> dict:
        """Graph at a glance: repo head plus node/edge counts. Good first call."""
        since = self._mark()
        nodes = self._q(
            "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS count ORDER BY label"
        )
        edges = self._q(
            "MATCH ()-[e]->() RETURN type(e) AS type, count(*) AS count ORDER BY type"
        )
        repo = self._q(
            "MATCH (r:Repo) RETURN r.name AS name, r.path AS path, r.head AS head,"
            " r.updated_at AS updated_at"
        )
        return self._envelope(
            "overview",
            "ok",
            {},
            {
                "repo": repo[0] if repo else None,
                "nodes": nodes,
                "edges": edges,
            },
            since=since,
        )

    def find_symbols(
        self, query: str, file: str | None = None, limit: int = 10
    ) -> dict:
        """Find symbols by name — exact qualname/name first, else substring.

        Substring search is case-insensitive and for *discovery* ("what is
        called *url*"); precise lookups should use exact names. ``file``
        scopes both modes.
        """
        since = self._mark()
        limit = _clamp_limit(limit)
        query = query.strip()
        params = {"q": query, "limit": limit}
        if file:
            params["file"] = file
        scope = " AND s.file = $file" if file else ""
        fields = (
            "s.file AS file, s.qualname AS qualname, s.name AS name,"
            " s.kind AS kind, s.lineno AS lineno"
        )
        exact = self._q(
            f"""
            MATCH (s:Symbol) WHERE (s.qualname = $q OR s.name = $q){scope}
            RETURN {fields}
            ORDER BY s.file, s.qualname
            LIMIT $limit
            """,
            params,
        )
        if exact:
            return self._envelope(
                "find_symbols", "ok", params, exact, ["exact match"], since
            )
        near = self._q(
            f"""
            MATCH (s:Symbol) WHERE toLower(s.name) CONTAINS toLower($q){scope}
            RETURN {fields}
            ORDER BY s.name, s.file
            LIMIT $limit
            """,
            params,
        )
        status = "ok" if near else "not_found"
        notes = ["substring matches (no exact name hit)"] if near else []
        return self._envelope("find_symbols", status, params, near, notes, since)

    def callers(
        self,
        symbol: str,
        file: str | None = None,
        depth: int = 1,
        include_weak: bool = False,
        limit: int = 25,
    ) -> dict:
        """Who calls this symbol? Upstream walk over CALLS edges, 1..depth hops.

        One row per distinct caller with the shortest chain found, per-hop
        call-site counts, and weak flags. This is the impact primitive:
        depth 1 = direct callers, depth 2–3 = "what breaks if this changes".
        Guessed (weak) edges are excluded unless ``include_weak=True``.
        """
        since = self._mark()
        depth = _clamp_depth(depth)
        limit = _clamp_limit(limit)
        params = {
            "symbol": symbol,
            "file": file,
            "depth": depth,
            "include_weak": include_weak,
            "limit": limit,
        }
        target, err = self._resolve("callers", symbol, file, since, params)
        if err:
            return err
        conditions = ["(c.file <> $file OR c.qualname <> $qualname)"]
        if not include_weak:
            conditions.append(
                "all(r IN relationships(p) WHERE coalesce(r.weak, false) = false)"
            )
        cap = max(limit * 8, 200)
        rows = self._q(
            f"""
            MATCH p=(c:Symbol)-[:CALLS*1..{depth}]->
                    (t:Symbol {{file: $file, qualname: $qualname}})
            WHERE {" AND ".join(conditions)}
            RETURN c.file AS file, c.qualname AS caller, length(p) AS hops,
                   [n IN nodes(p) | {{file: n.file, qualname: n.qualname}}] AS chain,
                   [r IN relationships(p) | coalesce(r.count, 0)] AS counts,
                   [r IN relationships(p) | coalesce(r.weak, false)] AS weaks
            ORDER BY hops, file, caller
            LIMIT {cap}
            """,
            {
                "file": target["file"],
                "qualname": target["qualname"],
            },
        )
        deduped: dict[SymbolId, dict] = {}
        for row in rows:
            key = (row["file"], row["caller"])
            if key not in deduped:  # rows arrive sorted by hops: keep shortest
                deduped[key] = row
        results = sorted(
            deduped.values(), key=lambda r: (r["hops"], r["file"], r["caller"])
        )[:limit]
        notes = [f"target = {target['qualname']}"]
        if len(deduped) > limit:
            # Say what we hide: a limit cut must not read as a total
            # (REVIEW-M4R Gate C O1). "at least" when the raw walk itself
            # hit its cap, so the count is a floor, not a census.
            scope = "at least " if len(rows) >= cap else ""
            notes.append(
                f"showing {limit} of {scope}{len(deduped)} distinct callers"
                f" (limit={limit})"
            )
        if not include_weak:
            notes.extend(
                self._hidden_weak_note("caller", "in", target, depth, cap, deduped)
            )
        return self._envelope("callers", "ok", params, results, notes, since)

    def callees(
        self,
        symbol: str,
        file: str | None = None,
        depth: int = 1,
        include_weak: bool = False,
        limit: int = 25,
    ) -> dict:
        """What does this symbol call? Downstream walk over CALLS edges.

        Mirror of :meth:`callers` — comprehension and dependency checks
        ("what does this route actually touch").
        """
        since = self._mark()
        depth = _clamp_depth(depth)
        limit = _clamp_limit(limit)
        params = {
            "symbol": symbol,
            "file": file,
            "depth": depth,
            "include_weak": include_weak,
            "limit": limit,
        }
        source, err = self._resolve("callees", symbol, file, since, params)
        if err:
            return err
        conditions = ["(c.file <> $file OR c.qualname <> $qualname)"]
        if not include_weak:
            conditions.append(
                "all(r IN relationships(p) WHERE coalesce(r.weak, false) = false)"
            )
        cap = max(limit * 8, 200)
        rows = self._q(
            f"""
            MATCH p=(s:Symbol {{file: $file, qualname: $qualname}})
                    -[:CALLS*1..{depth}]->
                    (c:Symbol)
            WHERE {" AND ".join(conditions)}
            RETURN c.file AS file, c.qualname AS callee, length(p) AS hops,
                   [n IN nodes(p) | {{file: n.file, qualname: n.qualname}}] AS chain,
                   [r IN relationships(p) | coalesce(r.count, 0)] AS counts,
                   [r IN relationships(p) | coalesce(r.weak, false)] AS weaks
            ORDER BY hops, file, callee
            LIMIT {cap}
            """,
            {"file": source["file"], "qualname": source["qualname"]},
        )
        deduped: dict[SymbolId, dict] = {}
        for row in rows:
            key = (row["file"], row["callee"])
            if key not in deduped:
                deduped[key] = row
        results = sorted(
            deduped.values(), key=lambda r: (r["hops"], r["file"], r["callee"])
        )[:limit]
        notes = [f"source = {source['qualname']}"]
        if len(deduped) > limit:
            # Mirror of `callers`: the limit cut is disclosed, not silent.
            scope = "at least " if len(rows) >= cap else ""
            notes.append(
                f"showing {limit} of {scope}{len(deduped)} distinct callees"
                f" (limit={limit})"
            )
        if not include_weak:
            notes.extend(
                self._hidden_weak_note("callee", "out", source, depth, cap, deduped)
            )
        return self._envelope("callees", "ok", params, results, notes, since)

    def importers(self, module: str, limit: int = 50) -> dict:
        """Files that import a module, with import line numbers.

        ``module`` is the dotted module path as ingested (e.g.
        ``flask.helpers``) — see ``docs/graph-model.md`` §IMPORTS.
        """
        since = self._mark()
        limit = _clamp_limit(limit)
        params = {"module": module, "limit": limit}
        rows = self._q(
            """
            MATCH (f:File)-[i:IMPORTS]->(m:Module {name: $module})
            RETURN f.path AS file, i.line AS line
            ORDER BY file, line
            LIMIT $limit
            """,
            params,
        )
        notes = [] if rows else ["no imports recorded for this module key"]
        status = "ok" if rows else "not_found"
        return self._envelope("importers", status, params, rows, notes, since)

    def file_history(self, path: str, limit: int = 20) -> dict:
        """Commits that touched a file, newest first, with churn stats.

        ``added``/``removed`` are line counts from ``git log --numstat``
        (``None`` for binary diffs).
        """
        since = self._mark()
        limit = _clamp_limit(limit)
        params = {"path": path, "limit": limit}
        rows = self._q(
            """
            MATCH (c:Commit)-[m:MODIFIED]->(f:File {path: $path})
            RETURN c.sha AS sha, c.ts AS ts, c.summary AS summary,
                   coalesce(c.author_name, '') AS author,
                   m.added AS added, m.removed AS removed
            ORDER BY c.ts DESC, c.seq ASC
            LIMIT $limit
            """,
            params,
        )
        notes = [] if rows else ["path not found in tree or history"]
        status = "ok" if rows else "not_found"
        return self._envelope("file_history", status, params, rows, notes, since)

    def owners(self, path: str, limit: int = 5) -> dict:
        """Reviewer candidates for a file: authors ranked by commits touching it.

        Ordered by touch count, then recency; timestamps let callers weight
        area experts vs one-off fixers.
        """
        since = self._mark()
        limit = _clamp_limit(limit)
        params = {"path": path, "limit": limit}
        rows = self._q(
            """
            MATCH (c:Commit)-[:MODIFIED]->(f:File {path: $path})
            MATCH (a:Author)-[:AUTHORED]->(c)
            WITH a, count(DISTINCT c) AS commits,
                 max(c.ts) AS last_ts, min(c.ts) AS first_ts
            RETURN a.name AS name, a.email AS email, commits, last_ts, first_ts
            ORDER BY commits DESC, last_ts DESC
            LIMIT $limit
            """,
            params,
        )
        notes = [] if rows else ["no author history for this path"]
        status = "ok" if rows else "not_found"
        return self._envelope("owners", status, params, rows, notes, since)

    def search_commits(self, text: str, limit: int = 20) -> dict:
        """Case-insensitive substring search over commit messages.

        The archaeology primitive: "why is this like this" starts by finding
        the commits that said so, then reading files/decisions around them.
        """
        since = self._mark()
        limit = _clamp_limit(limit)
        params = {"text": text, "limit": limit}
        rows = self._q(
            """
            MATCH (c:Commit)
            WHERE toLower(coalesce(c.message, '')) CONTAINS toLower($text)
            RETURN c.sha AS sha, c.ts AS ts, c.summary AS summary,
                   coalesce(c.author_name, '') AS author
            ORDER BY c.ts DESC, c.seq ASC
            LIMIT $limit
            """,
            params,
        )
        status = "ok" if rows else "not_found"
        return self._envelope("search_commits", status, params, rows, [], since)

    def recall(
        self,
        query: str | None = None,
        include_invalid: bool = False,
        as_of: int | None = None,
        limit: int = 10,
    ) -> dict:
        """Search recorded memory: facts and past sessions (M3a episodes).

        Memory is bi-temporal (see ``archivist.memory``). Default view is
        "true now": only facts whose window is still open. ``include_invalid``
        returns full history with validity windows; ``as_of=<epoch>`` asks
        "what was true at T" (event time). Facts are never deleted, so an
        invalidated fact is still one query away. With no ``query`` string
        this lists the most recent memory — the "what did we do last time"
        call.
        """
        since = self._mark()
        limit = _clamp_limit(limit)
        params: dict[str, Any] = {
            "query": query,
            "include_invalid": include_invalid,
            "as_of": as_of,
            "limit": limit,
        }
        fact_params: dict[str, Any] = {"limit": limit}
        episode_params: dict[str, Any] = {"limit": limit}
        if query:
            fact_params["query"] = query
            episode_params["query"] = query
        if as_of is not None:
            fact_params["as_of"] = as_of
            episode_params["as_of"] = as_of

        fact_where: list[str] = []
        if query:
            fact_where.append("toLower(f.text) CONTAINS toLower($query)")
        if as_of is not None:
            fact_where.append(
                "coalesce(f.valid_at, f.created_at) <= $as_of"
                " AND (f.invalid_at IS NULL OR f.invalid_at > $as_of)"
            )
        elif not include_invalid:
            fact_where.append("f.invalid_at IS NULL")
        fact_filter = (" WHERE " + " AND ".join(fact_where)) if fact_where else ""
        facts = self._q(
            f"""
            MATCH (f:Fact){fact_filter}
            OPTIONAL MATCH (e:Episode)-[:PRODUCED]->(f)
            RETURN f.id AS id, f.text AS text, f.kind AS kind,
                   coalesce(f.valid_at, f.created_at) AS ts,
                   f.valid_at AS valid_at, f.invalid_at AS invalid_at,
                   f.created_at AS created_at, f.expired_at AS expired_at,
                   collect(e.id) AS episode_ids, collect(e.kind) AS episode_kinds
            ORDER BY coalesce(f.valid_at, f.created_at) DESC, f.id
            LIMIT $limit
            """,
            fact_params,
        )

        episode_where: list[str] = []
        if query:
            episode_where.append(
                "(toLower(coalesce(e.question, '')) CONTAINS toLower($query)"
                " OR toLower(coalesce(e.answer, '')) CONTAINS toLower($query))"
            )
        if as_of is not None:
            episode_where.append("coalesce(e.ts, e.created_at) <= $as_of")
        episode_filter = (
            (" WHERE " + " AND ".join(episode_where)) if episode_where else ""
        )
        episodes = self._q(
            f"""
            MATCH (e:Episode){episode_filter}
            RETURN e.id AS id, e.kind AS kind, e.source AS source,
                   coalesce(e.ts, e.created_at) AS ts,
                   e.question AS question, e.answer AS answer
            ORDER BY coalesce(e.ts, e.created_at) DESC, e.id
            LIMIT $limit
            """,
            episode_params,
        )

        facts_fetched, sessions_fetched = len(facts), len(episodes)
        notes: list[str] = []
        truncated = 0
        rows: list[dict] = []
        for row in facts:
            # A fact can have several producing episodes over time; the
            # first is enough for the row view (lineage stays on the edges).
            eps = [e for e in (row.pop("episode_ids") or []) if e]
            kinds = [k for k in (row.pop("episode_kinds") or []) if k]
            row["episode_id"] = eps[0] if eps else None
            row["episode_kind"] = kinds[0] if kinds else None
            rows.append({**row, "type": "fact"})
        for row in episodes:
            row = {**row, "type": "episode"}
            if isinstance(row.get("answer"), str) and len(row["answer"]) > 400:
                row["answer"] = row["answer"][:399] + "…"
                truncated += 1
            rows.append(row)
        rows.sort(key=lambda r: (-(r.get("ts") or 0), str(r.get("id") or "")))
        rows = rows[:limit]

        if include_invalid and as_of is None:
            notes.append("invalidated facts included (full history)")
        if as_of is not None:
            notes.append(f"as of {as_of} (event time)")
        # A cap that hides rows must say so (silent filtering is a bug class).
        if (
            facts_fetched >= limit
            or sessions_fetched >= limit
            or facts_fetched + sessions_fetched > limit
        ):
            notes.append(
                f"capped at limit={limit}: fetched {facts_fetched} fact(s) +"
                f" {sessions_fetched} session(s); more may exist"
            )
        if truncated:
            notes.append(f"{truncated} episode answers truncated for context")
        if rows:
            notes.insert(0, f"{len(facts)} facts, {len(episodes)} sessions")
        else:
            notes.append(
                f"nothing true as of {as_of}"
                if as_of is not None
                else "no memory matches (nothing recorded yet?)"
            )
        status = "ok" if rows else "not_found"
        return self._envelope("recall", status, params, rows, notes, since)

    def query(self, cypher: str, params: dict | None = None, limit: int = 100) -> dict:
        """Run a read-only Cypher escape hatch (for demos / ad-hoc inspection).

        Curated tools above keep answers reproducible; this is for questions
        we did not anticipate. Write queries belong to the ingest path, not
        here.
        """
        since = self._mark()
        limit = _clamp_limit(limit)
        rows = self._q(cypher, params)
        return self._envelope(
            "query", "ok", {"cypher": cypher, **(params or {})}, rows[:limit], [], since
        )
