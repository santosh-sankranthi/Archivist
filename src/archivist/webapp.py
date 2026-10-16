"""Web console for Archivist — chat + graph explorer + trace panel (M4a).

Why stdlib and no build step: same call as :mod:`archivist.llm` — the demo
box is a 3.8 GB WSL instance, and an auditable product should not need a
node_modules tree to show its work. Every JSON endpoint below is a
projection over the same read-only queries the CLI tools run (``ro_query``
is the only gate), so the console cannot drift from the graph the reviews
verified: it shows what the graph holds — or nothing. (Console v2, built
by Vite into ``console/dist``, is served at ``/console/`` — a box without
the build gets an honest 503, not a broken page.)

Endpoints:

    GET  /                     static console (index.html)
    GET  /static/<path>        static assets (css/js/vendor)
    GET  /console/             Console v2 — the built Vite app (console/dist)
    GET  /api/state            graph + memory summary + LLM status
    GET  /api/graph/overview   hubs: most-called symbols, most-imported modules
    GET  /api/graph/activity   recent commits (sha, ts, summary, author, files)
    GET  /api/graph/find       symbol + file discovery search
    GET  /api/graph/file       one file: symbols, imports, recent history
    GET  /api/graph/module     files importing one module
    GET  /api/graph/symbol     call neighbourhood (callers + callees, ≤3 hops)
    GET  /api/graph/path       shortest directed call path between two symbols
    POST /api/cypher           read-only escape hatch, projected
    POST /api/ask              run the agent loop (503 until a key is set)

The server is bounded: at most ``MAX_INFLIGHT`` requests are served at once,
so the console can face a tunnel (M5) without turning a burst of stalled
connections into unbounded threads on a 4.8 GB box.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from falkordb import Edge, FalkorDB, Node
from falkordb import Path as GraphPath
from redis.exceptions import ResponseError

from .agent import Agent
from .llm import ChatLLM
from .memory import Memory
from .tools import RepoGraph

WEB_DIR = Path(__file__).resolve().parent / "web"
# Console v2 (M4R): the built Vite app. Not committed — a box without the
# build serves an honest 503 from `_console`, never a broken page.
CONSOLE_DIR = Path(__file__).resolve().parents[2] / "console" / "dist"

# Display caps: a console page must stay renderable; every cap is *reported*
# in ``notes`` rather than silently applied (same rule as the tools).
MAX_NODES = 260
MAX_EDGES = 520
MAX_SYMBOLS_PER_FILE = 140
MAX_IMPORTS_PER_FILE = 80
MAX_IMPORTERS_PER_MODULE = 150
MAX_BODY = 1_000_000  # POST bodies beyond this are refused (413) before parsing
# Public-exposure guard (M5): in-flight requests are capped so a tunnel in
# front of the console cannot amplify a flood of connections into a flood of
# threads. Over-cap connections are refused fast (503 + Retry-After), never
# queued; the cap is a safety valve, not a rate limiter.
MAX_INFLIGHT = 48

LLM_HINT = (
    "no LLM configured — set ARCHIVIST_LLM_BASE_URL, ARCHIVIST_LLM_API_KEY and "
    "ARCHIVIST_LLM_MODEL (or OPENAI_* equivalents) in the environment or the "
    "repo-root .env, then restart the server"
)

# Honest error codes per status: a refusal is not a "bad request", and a
# missing key is not a client error (REVIEW-M4a F5).
_ERROR_CODES = {
    400: "bad_request",
    403: "forbidden",
    404: "not_found",
    413: "too_large",
    503: "unavailable",
}

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


class ApiError(Exception):
    """A clean 4xx for the JSON layer."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _jsonable(value):
    """Make a FalkorDB property value JSON-safe without lying about types."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def collect(result_set, columns) -> tuple[dict, dict, list[dict]]:
    """Flatten raw FalkorDB rows into ``(nodes, edges, scalar rows)``.

    Graph entities become renderable dicts keyed by their server ids; cells
    that are plain values land in ``scalars`` so the escape hatch can show
    aggregates too. Nothing is invented: what the query returned is what
    comes out.
    """
    nodes: dict[int, dict] = {}
    edges: dict[int, dict] = {}

    def add_node(node: Node) -> None:
        if node.id not in nodes:
            nodes[node.id] = {
                "id": node.id,
                "type": (node.labels or ["Node"])[0],
                "props": _jsonable(node.properties),
            }

    def add_edge(edge: Edge) -> None:
        if edge.id not in edges:
            edges[edge.id] = {
                "id": edge.id,
                "rel": edge.relation,
                "source": edge.src_node,
                "target": edge.dest_node,
                "props": _jsonable(edge.properties),
            }

    def take(value) -> bool:
        if isinstance(value, Node):
            add_node(value)
            return True
        if isinstance(value, Edge):
            add_edge(value)
            return True
        if isinstance(value, GraphPath):
            for node in value.nodes():
                add_node(node)
            for edge in value.edges():
                add_edge(edge)
            return True
        return False

    scalars: list[dict] = []
    for row in result_set:
        scalar_row: dict = {}
        for column, value in zip(columns, row, strict=True):
            if not take(value):
                # Keep NULL columns: "what the query returned" includes nulls,
                # and a dropped column is indistinguishable from an absent one.
                scalar_row[column] = _jsonable(value)
        if scalar_row:
            scalars.append(scalar_row)
    return nodes, edges, scalars


def finalize(
    nodes: dict[int, dict],
    edges: dict[int, dict],
    *,
    focus: int | None = None,
    notes: list[str] | None = None,
) -> dict:
    """Drop orphans, apply display caps, and say so. Returns the payload."""
    notes = list(notes or [])

    kept_edges = {
        eid: edge
        for eid, edge in edges.items()
        if edge["source"] in nodes and edge["target"] in nodes
    }
    if len(kept_edges) != len(edges):
        notes.append(
            f"{len(edges) - len(kept_edges)} edge(s) dropped — endpoint node(s) "
            "not in result; include endpoints in RETURN to plot"
        )
    edges = kept_edges

    if len(nodes) > MAX_NODES:
        notes.append(
            f"display capped at {MAX_NODES} nodes ({len(nodes) - MAX_NODES} not shown)"
        )
        nodes = dict(list(nodes.items())[:MAX_NODES])
        edges = {
            eid: edge
            for eid, edge in edges.items()
            if edge["source"] in nodes and edge["target"] in nodes
        }
    if len(edges) > MAX_EDGES:
        notes.append(
            f"display capped at {MAX_EDGES} edges ({len(edges) - MAX_EDGES} not shown)"
        )
        edges = dict(list(edges.items())[:MAX_EDGES])

    if focus is not None and focus in nodes:
        nodes[focus]["focus"] = True
    return {
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
        "notes": notes,
    }


class WebApp:
    """Request routing over one repo graph. One instance per server."""

    def __init__(
        self, graph: str = "repo:flask", host: str = "127.0.0.1", port: int = 6379
    ):
        self.graph = graph
        self.host = host
        self.port = port

    # -- plumbing ----------------------------------------------------------

    def _ro(self, cypher: str, params: dict | None = None):
        """One short-lived read-only client per query, closed in ``finally``.

        A fresh connection is cheap on localhost and keeps shared state at
        zero across threaded requests; closing here keeps the process fd
        count flat over long console sessions (REVIEW-M4a F1).
        """
        db = FalkorDB(host=self.host, port=self.port)
        try:
            return db.select_graph(self.graph).ro_query(cypher, params or {})
        finally:
            db.close()

    def _project(
        self, cypher: str, params: dict | None = None, *, focus=None, notes=None
    ) -> dict:
        result = self._ro(cypher, params)
        columns = [header[1] for header in result.header]
        nodes, edges, _ = collect(result.result_set, columns)
        return finalize(nodes, edges, focus=focus, notes=notes)

    @staticmethod
    def _json(status: int, payload: dict) -> tuple[int, str, bytes]:
        body = json.dumps(payload, default=str).encode("utf-8")
        return status, "application/json; charset=utf-8", body

    # -- dispatch ----------------------------------------------------------

    def dispatch(
        self, method: str, path: str, query: dict, body: dict
    ) -> tuple[int, str, bytes]:
        try:
            if method == "GET":
                if path == "/":
                    return self._static("index.html")
                if path.startswith("/static/"):
                    return self._static(path[len("/static/") :])
                if path == "/console" or path.startswith("/console/"):
                    return self._console(path[len("/console") :])
                if path == "/api/state":
                    return self._json(200, self._state())
                if path == "/api/graph/overview":
                    return self._json(200, self._graph_overview())
                if path == "/api/graph/activity":
                    return self._json(200, self._graph_activity())
                if path == "/api/graph/find":
                    return self._json(200, self._graph_find(query))
                if path == "/api/graph/file":
                    return self._json(200, self._graph_file(query))
                if path == "/api/graph/module":
                    return self._json(200, self._graph_module(query))
                if path == "/api/graph/symbol":
                    return self._json(200, self._graph_symbol(query))
                if path == "/api/graph/path":
                    return self._json(200, self._graph_path(query))
            elif method == "POST":
                if path == "/api/cypher":
                    return self._json(200, self._cypher(body))
                if path == "/api/ask":
                    return self._json(200, self._ask(body))
            return self._json(
                404, {"error": "not_found", "message": f"no route {method} {path}"}
            )
        except ApiError as exc:
            return self._json(
                exc.status,
                {
                    "error": _ERROR_CODES.get(exc.status, "error"),
                    "message": exc.message,
                },
            )
        except ResponseError as exc:
            # Client-class failure (bad Cypher, refused write), not a server fault.
            text = str(exc)
            if "read-only" in text.lower():
                return self._json(403, {"error": "forbidden", "message": text})
            return self._json(400, {"error": "bad_request", "message": text})
        # One bad route must not kill the console — surface it as JSON.
        except Exception as exc:  # noqa: BLE001
            return self._json(
                500,
                {"error": "server_error", "message": f"{type(exc).__name__}: {exc}"},
            )

    # -- static ------------------------------------------------------------

    def _static(self, rel: str) -> tuple[int, str, bytes]:
        target = (WEB_DIR / rel).resolve()
        if not target.is_relative_to(WEB_DIR) or not target.is_file():
            return self._json(404, {"error": "not_found", "message": f"no asset {rel}"})
        content_type = _CONTENT_TYPES.get(target.suffix, "application/octet-stream")
        return 200, content_type, target.read_bytes()

    def _console(self, rel: str) -> tuple[int, str, bytes]:
        """Serve the built Console v2 at ``/console/`` (SPA fallback included).

        Contract, in order: a path that escapes the dist directory is a 404
        (whatever spelling); an unbuilt dist is an honest 503 (the box was
        never asked to build it); a missing file with an extension is a 404
        (a stale asset reference, not a route); anything else is a client
        route and gets ``index.html``.
        """
        rel = rel.lstrip("/") or "index.html"
        target = (CONSOLE_DIR / rel).resolve()
        if not target.is_relative_to(CONSOLE_DIR):
            raise ApiError(404, "no such console asset")
        index = CONSOLE_DIR / "index.html"
        if not index.is_file():
            raise ApiError(
                503,
                "console v2 is not built on this box — run `npm run build` "
                "in console/, then reload /console/",
            )
        if target.is_file():
            content_type = _CONTENT_TYPES.get(target.suffix, "application/octet-stream")
            return 200, content_type, target.read_bytes()
        if Path(rel).suffix:
            raise ApiError(404, f"no console asset {rel}")
        return 200, _CONTENT_TYPES[".html"], index.read_bytes()

    # -- GET handlers ------------------------------------------------------

    def _state(self) -> dict:
        repo = RepoGraph(graph=self.graph, host=self.host, port=self.port)
        try:
            overview = repo.overview()["results"]
        finally:
            repo.close()
        counts = {row["label"]: row["count"] for row in overview["nodes"]}
        latest = self._ro("""
            MATCH (e:Episode)
            RETURN e.id AS id, e.kind AS kind, e.source AS source,
                   coalesce(e.ts, e.created_at) AS ts, e.question AS question
            ORDER BY ts DESC, e.id DESC LIMIT 1
            """).result_set
        llm = ChatLLM()
        return {
            "graph": self.graph,
            "repo": overview["repo"],
            "nodes": overview["nodes"],
            "edges": overview["edges"],
            "memory": {
                "sessions": counts.get("Episode", 0),
                "facts": counts.get("Fact", 0),
                "latest": (
                    {
                        "id": latest[0][0],
                        "kind": latest[0][1],
                        "source": latest[0][2],
                        "ts": latest[0][3],
                        "question": latest[0][4],
                    }
                    if latest
                    else None
                ),
            },
            "llm": {"configured": llm.is_configured(), "model": llm.model or None},
        }

    def _graph_overview(self) -> dict:
        hub_symbols = self._ro("""
            MATCH (c:Symbol)-[e:CALLS]->(s:Symbol)
            WHERE e.weak = false
            WITH s, count(DISTINCT c) AS callers, sum(e.count) AS calls
            RETURN s.file AS file, s.qualname AS qualname, s.name AS name,
                   s.kind AS kind, callers, calls
            ORDER BY callers DESC, calls DESC, s.file, s.qualname
            LIMIT 10
            """)
        hub_modules = self._ro("""
            MATCH (f:File)-[i:IMPORTS]->(m:Module)
            WITH m, count(DISTINCT f) AS importers, count(i) AS sites
            RETURN m.name AS name, importers, sites
            ORDER BY importers DESC, sites DESC, m.name
            LIMIT 10
            """)
        return {
            "hubs": {
                "symbols": [
                    {
                        "file": r[0],
                        "qualname": r[1],
                        "name": r[2],
                        "kind": r[3],
                        "callers": r[4],
                        "calls": r[5],
                    }
                    for r in hub_symbols.result_set
                ],
                "modules": [
                    {"name": r[0], "importers": r[1], "sites": r[2]}
                    for r in hub_modules.result_set
                ],
            }
        }

    def _graph_activity(self) -> dict:
        """The newest commits, straight from history (Home's activity list)."""
        rows = self._ro("""
            MATCH (c:Commit)
            OPTIONAL MATCH (c)<-[:AUTHORED]-(a:Author)
            OPTIONAL MATCH (c)-[:MODIFIED]->(f:File)
            WITH c, a, count(DISTINCT f) AS files
            RETURN c.sha AS sha, c.ts AS ts, c.summary AS summary,
                   a.name AS author, files
            ORDER BY c.ts DESC, c.seq ASC LIMIT 12
            """).result_set
        return {
            "commits": [
                {
                    "sha": (row[0] or "")[:10],
                    "ts": row[1],
                    "summary": row[2],
                    "author": row[3],
                    "files": row[4],
                }
                for row in rows
            ]
        }

    def _graph_find(self, query: dict) -> dict:
        q = (query.get("q") or [""])[0].strip()
        if not q:
            raise ApiError(400, "pass ?q=<text>")
        if len(q) > 200:
            raise ApiError(400, "query too long (200 chars max)")
        try:
            limit = max(1, min(20, int((query.get("limit") or ["12"])[0])))
        except ValueError:
            limit = 12
        repo = RepoGraph(graph=self.graph, host=self.host, port=self.port)
        try:
            envelope = repo.find_symbols(q, limit=limit)
        finally:
            repo.close()
        files = self._ro(
            """
            MATCH (f:File)
            WHERE toLower(f.path) CONTAINS toLower($q)
            RETURN f.path AS path, f.loc AS loc
            ORDER BY f.loc DESC, f.path
            LIMIT $limit
            """,
            {"q": q, "limit": max(4, limit // 2)},
        )
        return {
            "query": q,
            "symbols": envelope["results"],
            "files": [{"path": row[0], "loc": row[1]} for row in files.result_set],
            "notes": envelope["notes"],
        }

    def _graph_file(self, query: dict) -> dict:
        path = (query.get("path") or [""])[0]
        if not path:
            raise ApiError(400, "pass ?path=<repo-relative file>")
        symbols = self._ro(
            f"""
            MATCH (f:File {{path: $path}})-[d:DEFINES]->(s:Symbol)
            RETURN f, d, s ORDER BY s.lineno
            LIMIT {MAX_SYMBOLS_PER_FILE}
            """,
            {"path": path},
        )
        nodes, edges, _ = collect(
            symbols.result_set, [header[1] for header in symbols.header]
        )
        if not nodes:
            raise ApiError(404, f"no File `{path}` in {self.graph}")
        imports = self._ro(
            f"""
            MATCH (f:File {{path: $path}})-[i:IMPORTS]->(m:Module)
            RETURN f, i, m ORDER BY i.line
            LIMIT {MAX_IMPORTS_PER_FILE}
            """,
            {"path": path},
        )
        imp_nodes, imp_edges, _ = collect(
            imports.result_set, [header[1] for header in imports.header]
        )
        nodes.update(imp_nodes)
        edges.update(imp_edges)
        total_symbols = self._ro(
            "MATCH (f:File {path: $path})-[:DEFINES]->(s:Symbol) RETURN count(s) AS n",
            {"path": path},
        ).result_set
        total_imports = self._ro(
            "MATCH (f:File {path: $path})-[:IMPORTS]->(m:Module) RETURN count(m) AS n",
            {"path": path},
        ).result_set
        total_symbols = total_symbols[0][0] if total_symbols else 0
        total_imports = total_imports[0][0] if total_imports else 0
        notes = []
        if total_symbols > MAX_SYMBOLS_PER_FILE:
            notes.append(
                f"symbols capped at {MAX_SYMBOLS_PER_FILE} "
                f"({total_symbols - MAX_SYMBOLS_PER_FILE} not shown)"
            )
        if total_imports > MAX_IMPORTS_PER_FILE:
            notes.append(
                f"imports capped at {MAX_IMPORTS_PER_FILE} "
                f"({total_imports - MAX_IMPORTS_PER_FILE} not shown)"
            )
        history = self._ro(
            """
            MATCH (c:Commit)-[m:MODIFIED]->(f:File {path: $path})
            RETURN c.sha AS sha, c.ts AS ts, c.summary AS summary,
                   m.added AS added, m.removed AS removed
            ORDER BY c.ts DESC, c.seq ASC LIMIT 6
            """,
            {"path": path},
        )
        payload = finalize(nodes, edges, notes=notes)
        payload["file"] = next(
            (n["props"] for n in payload["nodes"] if n["type"] == "File"), {}
        )
        payload["history"] = [
            {
                "sha": r[0][:10],
                "ts": r[1],
                "summary": r[2],
                "added": r[3],
                "removed": r[4],
            }
            for r in history.result_set
        ]
        return payload

    def _graph_module(self, query: dict) -> dict:
        name = (query.get("name") or [""])[0]
        if not name:
            raise ApiError(400, "pass ?name=<module>")
        imports = self._ro(
            f"""
            MATCH (f:File)-[i:IMPORTS]->(m:Module {{name: $name}})
            RETURN f, i, m ORDER BY f.path
            LIMIT {MAX_IMPORTERS_PER_MODULE}
            """,
            {"name": name},
        )
        nodes, edges, _ = collect(
            imports.result_set, [header[1] for header in imports.header]
        )
        if not nodes:
            raise ApiError(404, f"no Module `{name}` in {self.graph}")
        total_importers = self._ro(
            "MATCH (f:File)-[:IMPORTS]->(m:Module {name: $name}) RETURN count(f) AS n",
            {"name": name},
        ).result_set
        total_importers = total_importers[0][0] if total_importers else 0
        notes = (
            [
                (
                    f"importers capped at {MAX_IMPORTERS_PER_MODULE} "
                    f"({total_importers - MAX_IMPORTERS_PER_MODULE} not shown)"
                )
            ]
            if total_importers > MAX_IMPORTERS_PER_MODULE
            else []
        )
        return finalize(nodes, edges, notes=notes)

    def _graph_symbol(self, query: dict) -> dict:
        path = (query.get("file") or [""])[0]
        qualname = (query.get("qualname") or [""])[0]
        if not path or not qualname:
            raise ApiError(400, "pass ?file=<path>&qualname=<qualname>")
        try:
            depth = max(1, min(3, int((query.get("depth") or ["1"])[0])))
        except ValueError:
            depth = 1
        include_weak = (query.get("weak") or ["0"])[0] in ("1", "true", "yes")
        params = {"file": path, "qualname": qualname}
        root = self._ro(
            "MATCH (s:Symbol {file: $file, qualname: $qualname}) RETURN s",
            params,
        )
        if not root.result_set:
            raise ApiError(404, f"no Symbol `{qualname}` in {path}")
        focus_id = root.result_set[0][0].id
        up = self._ro(
            f"""
            MATCH p = (a:Symbol)-[:CALLS*1..{depth}]->(s:Symbol {{file: $file, qualname: $qualname}})
            RETURN p ORDER BY length(p) LIMIT 70
            """,
            params,
        )
        down = self._ro(
            f"""
            MATCH p = (s:Symbol {{file: $file, qualname: $qualname}})-[:CALLS*1..{depth}]->(b:Symbol)
            RETURN p ORDER BY length(p) LIMIT 70
            """,
            params,
        )
        nodes, edges, _ = collect([row for row in root.result_set], ["s"])
        for result in (up, down):
            sub_nodes, sub_edges, _ = collect(
                result.result_set, [header[1] for header in result.header]
            )
            nodes.update(sub_nodes)
            edges.update(sub_edges)
        hidden = 0
        if not include_weak:
            kept = {}
            for eid, edge in edges.items():
                if edge["props"].get("weak") is True:
                    hidden += 1
                else:
                    kept[eid] = edge
            edges = kept
        notes = []
        if hidden:
            notes.append(
                f"{hidden} weak edge(s) hidden — resolver guesses, not verified "
                "(toggle 'show weak' to include)"
            )
        if len(up.result_set) >= 70 or len(down.result_set) >= 70:
            notes.append(
                "neighbourhood path cap reached (70 per direction at this depth)"
            )
        payload = finalize(nodes, edges, focus=focus_id, notes=notes)
        payload["stats"] = self._symbol_stats(params)
        return payload

    def _symbol_stats(self, params: dict) -> dict:
        rows = self._ro(
            """
            MATCH (s:Symbol {file: $file, qualname: $qualname})
            OPTIONAL MATCH (c:Symbol)-[ce:CALLS]->(s) WHERE ce.weak = false
            OPTIONAL MATCH (s)-[de:CALLS]->(d:Symbol) WHERE de.weak = false
            RETURN count(DISTINCT c) AS callers, count(DISTINCT d) AS callees
            """,
            params,
        ).result_set
        callers, callees = rows[0] if rows else (0, 0)
        return {"callers": callers, "callees": callees}

    def _graph_path(self, query: dict) -> dict:
        """Shortest directed call path between two symbols — honest either way.

        ``shortestPath`` is directed and only legal in WITH/RETURN
        (M2-RESEARCH), so a reverse leg is attempted explicitly before
        answering "no path", and the direction of a found path is stated,
        not implied. Depth bounds are interpolated because FalkorDB rejects
        parameterized var-length bounds — the value is clamped ints-only.
        Weak edges are never hidden here: a path that only exists through
        resolver guesses must say so, not disappear.
        """
        from_file = (query.get("from_file") or [""])[0]
        from_qual = (query.get("from_qualname") or [""])[0]
        to_file = (query.get("to_file") or [""])[0]
        to_qual = (query.get("to_qualname") or [""])[0]
        if not (from_file and from_qual and to_file and to_qual):
            raise ApiError(
                400,
                "pass ?from_file=&from_qualname=&to_file=&to_qualname=",
            )
        try:
            max_depth = max(1, min(12, int((query.get("max_depth") or ["8"])[0])))
        except ValueError:
            max_depth = 8

        def require(file: str, qual: str) -> int:
            rows = self._ro(
                "MATCH (s:Symbol {file: $file, qualname: $qualname}) RETURN s",
                {"file": file, "qualname": qual},
            ).result_set
            if not rows:
                raise ApiError(404, f"no Symbol `{qual}` in {file}")
            return rows[0][0].id

        from_id = require(from_file, from_qual)
        to_id = require(to_file, to_qual)
        endpoints = {
            "from": {"file": from_file, "qualname": from_qual},
            "to": {"file": to_file, "qualname": to_qual},
        }
        if from_id == to_id:
            return {
                "found": True,
                "direction": "same",
                "hops": 0,
                "nodes": [],
                "edges": [],
                "notes": ["same symbol on both ends — nothing to trace"],
                **endpoints,
            }

        def shortest(src: str, src_qual: str, dst: str, dst_qual: str):
            result = self._ro(
                f"""
                MATCH (a:Symbol {{file: $a_file, qualname: $a_qual}}),
                      (b:Symbol {{file: $b_file, qualname: $b_qual}})
                WITH shortestPath((a)-[:CALLS*1..{max_depth}]->(b)) AS p
                RETURN p
                """,
                {"a_file": src, "a_qual": src_qual, "b_file": dst, "b_qual": dst_qual},
            )
            rows = result.result_set
            return rows[0][0] if rows else None

        direction = "forward"
        path = shortest(from_file, from_qual, to_file, to_qual)
        if path is None:
            direction = "reverse"
            path = shortest(to_file, to_qual, from_file, from_qual)
        if path is None:
            return {
                "found": False,
                "direction": None,
                "hops": None,
                "nodes": [],
                "edges": [],
                "notes": [
                    f"no call path in either direction within {max_depth} hops",
                ],
                **endpoints,
            }
        nodes, edges, _ = collect([[path]], ["p"])
        hops = len(edges)
        weak = sum(1 for edge in edges.values() if edge["props"].get("weak") is True)
        notes = []
        if direction == "reverse":
            notes.append("no path from → to; this path runs to → from")
        if weak:
            notes.append(f"{weak} edge(s) on the path are resolver guesses (weak)")
        payload = finalize(nodes, edges, notes=notes)
        payload.update(
            {"found": True, "direction": direction, "hops": hops, **endpoints}
        )
        return payload

    # -- POST handlers -----------------------------------------------------

    def _cypher(self, body: dict) -> dict:
        cypher = body.get("cypher")
        if not isinstance(cypher, str) or not cypher.strip():
            raise ApiError(400, 'pass {"cypher": "..."}')
        if len(cypher) > 4000:
            raise ApiError(400, "query too long (4000 chars max)")
        params = body.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise ApiError(400, "params must be a JSON object")
        result = self._ro(cypher, params)
        columns = [header[1] for header in result.header]
        rows = result.result_set
        cap_note = []
        if len(rows) > 1000:
            cap_note.append(
                f"result capped at 1000 rows ({len(rows) - 1000} not shown)"
            )
            rows = rows[:1000]
        nodes, edges, scalars = collect(rows, columns)
        payload = finalize(nodes, edges, notes=cap_note)
        payload.update({"count": len(result.result_set), "scalars": scalars[:100]})
        return payload

    def _ask(self, body: dict) -> dict:
        question = body.get("question")
        if not isinstance(question, str) or not question.strip():
            raise ApiError(400, 'pass {"question": "..."}')
        if len(question) > 2000:
            raise ApiError(400, "question too long (2000 chars max)")
        try:
            max_steps = max(1, min(12, int(body.get("max_steps", 8))))
        except (TypeError, ValueError):
            max_steps = 8
        remember = body.get("remember", True) is not False
        llm = ChatLLM()
        if not llm.is_configured():
            raise ApiError(503, LLM_HINT)
        repo = RepoGraph(graph=self.graph, host=self.host, port=self.port)
        memory = (
            Memory(graph=self.graph, host=self.host, port=self.port)
            if remember
            else None
        )
        try:
            warnings: list[str] = []
            episode_id = None
            if memory is not None:
                try:
                    rows = repo.query("MATCH (r:Repo) RETURN r.head AS head")[
                        "results"
                    ] or [{}]
                    episode_id = memory.record_episode(
                        kind="agent-session",
                        source="webapp",
                        question=question,
                        graph=self.graph,
                        model=llm.model,
                        head=rows[0].get("head"),
                    )
                except Exception as exc:  # noqa: BLE001 - memory writes are best-effort
                    warnings.append(f"memory write failed: {exc}")
            answer = Agent(
                repo, llm, max_steps=max_steps, memory=memory, episode_id=episode_id
            ).ask(question)
            if memory is not None and episode_id is not None:
                try:
                    memory.record_episode(
                        id=episode_id,
                        answer=answer.text,
                        partial=answer.partial,
                        error=answer.error,
                        llm_calls=answer.llm_calls,
                        steps=len(answer.steps),
                        queries=len(answer.queries),
                    )
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"memory write failed: {exc}")
                    episode_id = None
            return {
                "configured": True,
                "answer": answer.to_dict(),
                "episode_id": episode_id,
                "warnings": warnings,
            }
        finally:
            repo.close()
            if memory is not None:
                memory.close()

    # -- helpers -----------------------------------------------------------

    def status_line(self) -> str:
        llm = ChatLLM()
        state = (
            "configured"
            if llm.is_configured()
            else "not configured (set ARCHIVIST_LLM_*)"
        )
        return f"graph={self.graph} llm={state}"


class _BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer with a hard cap on concurrently served requests.

    A slot is taken when a connection is accepted and released when its
    handler finishes. Over-cap connections get a bare 503 + Retry-After so
    clients can back off; ``max_inflight`` is clamped to >= 1.
    """

    daemon_threads = True

    def __init__(self, address, handler, *, max_inflight: int = MAX_INFLIGHT):
        super().__init__(address, handler)
        self._slots = threading.BoundedSemaphore(max(1, int(max_inflight)))

    def process_request(self, request, client_address) -> None:
        if not self._slots.acquire(blocking=False):
            body = (
                b'{"error": "unavailable", '
                b'"message": "server at capacity; retry shortly"}'
            )
            head = (
                "HTTP/1.1 503 Service Unavailable\r\n"
                "Content-Type: application/json; charset=utf-8\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Retry-After: 5\r\n"
                "Connection: close\r\n\r\n"
            )
            try:
                request.sendall(head.encode("ascii") + body)
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            # The worker thread never started, so its ``finally`` cannot
            # release the slot; release it here instead of leaking one.
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def make_server(
    app: WebApp,
    *,
    bind: str = "127.0.0.1",
    port: int = 8787,
    max_inflight: int = MAX_INFLIGHT,
) -> ThreadingHTTPServer:
    """A threaded HTTP server for ``app``; port 0 picks a free port (tests).

    At most ``max_inflight`` requests are served at once
    (:data:`MAX_INFLIGHT` by default); over-cap connections get 503 +
    Retry-After instead of an unbounded worker thread.
    """

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "Archivist/0.4"
        sys_version = ""  # don't leak the interpreter version in Server:
        timeout = 15  # a stalled socket must not pin a worker thread forever

        def version_string(self) -> str:
            return self.server_version

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_HEAD(self):
            self._dispatch("HEAD")

        def do_PUT(self):
            self._dispatch("PUT")

        def do_PATCH(self):
            self._dispatch("PATCH")

        def do_DELETE(self):
            self._dispatch("DELETE")

        def do_OPTIONS(self):
            self._dispatch("OPTIONS")

        def _dispatch(self, method: str) -> None:
            close_after = False
            try:
                split = urlsplit(self.path)
                body: dict = {}
                if method == "POST":
                    length = int(self.headers.get("Content-Length") or 0)
                    if length < 0:
                        raise ValueError("bad Content-Length")
                    if length > MAX_BODY:
                        close_after = True  # unread body: don't reuse the socket
                        raise ApiError(
                            413,
                            f"request body too large ({length} bytes; max {MAX_BODY})",
                        )
                    raw = self.rfile.read(length) if length else b""
                    parsed = json.loads(raw.decode("utf-8") or "{}")
                    if not isinstance(parsed, dict):
                        raise ValueError("body must be a JSON object")
                    body = parsed
                status, content_type, payload = self.server.app.dispatch(  # type: ignore[attr-defined]
                    method, split.path, parse_qs(split.query), body
                )
            except ApiError as exc:
                status, content_type, payload = WebApp._json(
                    exc.status,
                    {
                        "error": _ERROR_CODES.get(exc.status, "error"),
                        "message": exc.message,
                    },
                )
            except (ValueError, json.JSONDecodeError) as exc:
                status, content_type, payload = WebApp._json(
                    400, {"error": "bad_request", "message": f"bad request: {exc}"}
                )
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if method != "HEAD":
                self.wfile.write(payload)
            if close_after:
                self.close_connection = True

        def log_message(self, format: str, *args) -> None:
            if os.environ.get("ARCHIVIST_WEB_LOG"):
                print(f"[web] {self.address_string()} {format % args}")

    server = _BoundedThreadingHTTPServer(
        (bind, port), Handler, max_inflight=max_inflight
    )
    server.app = app  # type: ignore[attr-defined]
    return server
