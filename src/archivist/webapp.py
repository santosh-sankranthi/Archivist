"""Web console for Archivist — chat + graph explorer + trace panel (M4a).

Why stdlib and no build step: same call as :mod:`archivist.llm` — the demo
box is a 3.8 GB WSL instance, and an auditable product should not need a
node_modules tree to show its work. Every JSON endpoint below is a
projection over the same read-only queries the CLI tools run (``ro_query``
is the only gate), so the console cannot drift from the graph the reviews
verified: it shows what the graph holds — or nothing.

Endpoints:

    GET  /                     static console (index.html)
    GET  /static/<path>        static assets (css/js/vendor)
    GET  /api/state            graph + memory summary + LLM status
    GET  /api/graph/overview   hubs: most-called symbols, most-imported modules
    GET  /api/graph/find       symbol + file discovery search
    GET  /api/graph/file       one file: symbols, imports, recent history
    GET  /api/graph/module     files importing one module
    GET  /api/graph/symbol     call neighbourhood (callers + callees, ≤3 hops)
    POST /api/cypher           read-only escape hatch, projected
    POST /api/ask              run the agent loop (503 until a key is set)
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from falkordb import Edge, FalkorDB, Node
from falkordb import Path as GraphPath

from .agent import Agent
from .llm import ChatLLM
from .memory import Memory
from .tools import RepoGraph

WEB_DIR = Path(__file__).resolve().parent / "web"

# Display caps: a console page must stay renderable; every cap is *reported*
# in ``notes`` rather than silently applied (same rule as the tools).
MAX_NODES = 260
MAX_EDGES = 520
MAX_SYMBOLS_PER_FILE = 140
MAX_IMPORTS_PER_FILE = 80
MAX_IMPORTERS_PER_MODULE = 150

LLM_HINT = (
    "no LLM configured — set ARCHIVIST_LLM_BASE_URL, ARCHIVIST_LLM_API_KEY and "
    "ARCHIVIST_LLM_MODEL (or OPENAI_* equivalents) in the environment or the "
    "repo-root .env, then restart the server"
)

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
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
            if not take(value) and value is not None:
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
            f"{len(edges) - len(kept_edges)} edge(s) dropped (endpoint not in result)"
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

    def _client(self):
        """A fresh read-only client per request; cheap and thread-safe."""
        return FalkorDB(host=self.host, port=self.port).select_graph(self.graph)

    def _ro(self, cypher: str, params: dict | None = None):
        return self._client().ro_query(cypher, params or {})

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
                if path == "/api/state":
                    return self._json(200, self._state())
                if path == "/api/graph/overview":
                    return self._json(200, self._graph_overview())
                if path == "/api/graph/find":
                    return self._json(200, self._graph_find(query))
                if path == "/api/graph/file":
                    return self._json(200, self._graph_file(query))
                if path == "/api/graph/module":
                    return self._json(200, self._graph_module(query))
                if path == "/api/graph/symbol":
                    return self._json(200, self._graph_symbol(query))
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
                exc.status, {"error": "bad_request", "message": exc.message}
            )
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

    # -- GET handlers ------------------------------------------------------

    def _state(self) -> dict:
        repo = RepoGraph(graph=self.graph, host=self.host, port=self.port)
        overview = repo.overview()["results"]
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
        envelope = repo.find_symbols(q, limit=limit)
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
        notes = []
        if len(symbols.result_set) >= MAX_SYMBOLS_PER_FILE:
            notes.append(f"symbols capped at {MAX_SYMBOLS_PER_FILE}")
        if len(imports.result_set) >= MAX_IMPORTS_PER_FILE:
            notes.append(f"imports capped at {MAX_IMPORTS_PER_FILE}")
        history = self._ro(
            """
            MATCH (c:Commit)-[m:MODIFIED]->(f:File {path: $path})
            RETURN c.sha AS sha, c.ts AS ts, c.summary AS summary,
                   m.added AS added, m.removed AS removed
            ORDER BY c.seq DESC LIMIT 6
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
        notes = (
            [f"importers capped at {MAX_IMPORTERS_PER_MODULE}"]
            if len(imports.result_set) >= MAX_IMPORTERS_PER_MODULE
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
            "MATCH (s:Symbol {file: $file, qualname: $qualname}) " "RETURN s",
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

    # -- POST handlers -----------------------------------------------------

    def _cypher(self, body: dict) -> dict:
        cypher = body.get("cypher")
        if not isinstance(cypher, str) or not cypher.strip():
            raise ApiError(400, 'pass {"cypher": "..."}')
        if len(cypher) > 4000:
            raise ApiError(400, "query too long (4000 chars max)")
        params = body.get("params") if isinstance(body.get("params"), dict) else {}
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

    # -- helpers -----------------------------------------------------------

    def status_line(self) -> str:
        llm = ChatLLM()
        state = (
            "configured"
            if llm.is_configured()
            else "not configured (set ARCHIVIST_LLM_*)"
        )
        return f"graph={self.graph} llm={state}"


def make_server(
    app: WebApp, *, bind: str = "127.0.0.1", port: int = 8787
) -> ThreadingHTTPServer:
    """A threaded HTTP server for ``app``; port 0 picks a free port (tests)."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "Archivist/0.4"

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def _dispatch(self, method: str) -> None:
            try:
                split = urlsplit(self.path)
                body: dict = {}
                if method == "POST":
                    length = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(length) if length else b""
                    parsed = json.loads(raw.decode("utf-8") or "{}")
                    if not isinstance(parsed, dict):
                        raise ValueError("body must be a JSON object")
                    body = parsed
                status, content_type, payload = self.server.app.dispatch(  # type: ignore[attr-defined]
                    method, split.path, parse_qs(split.query), body
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
            self.wfile.write(payload)

        def log_message(self, format: str, *args) -> None:
            if os.environ.get("ARCHIVIST_WEB_LOG"):
                print(f"[web] {self.address_string()} {format % args}")

    server = ThreadingHTTPServer((bind, port), Handler)
    server.daemon_threads = True
    server.app = app  # type: ignore[attr-defined]
    return server
