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
    GET  /api/graph/memory     memory timeline: episodes + bi-temporal facts
    GET  /api/graph/actions    proposed/closed actions (agent next steps)
    GET  /api/graph/crew       crew runs: phase spine + handoff notes
                               (as_of/history views)
    GET  /api/graph/history    repo (or one file's) past as UTC calendar-month
                               buckets — the time machine; ?from=&to= adds a
                               window of commits (and top files)
    GET  /api/graph/impact     blast radius of one symbol — the transitive
                               reverse-CALLS cone, with a file rollup
    GET  /api/graph/ownership  who knows what — authors ranked from the
                               diff-bearing history of the repo or one file
    POST /api/graph/induced    induced subgraph of an Ask step's evidence refs
    POST /api/actions/{id}/complete  close an action (done/superseded), auth-gated
    POST /api/cypher           read-only escape hatch, projected
    POST /api/ask              run the agent loop (503 until a key is set)
    POST /api/ask/stream       the same run as Server-Sent Events (live steps
                               and streamed answer deltas; done/error terminal)
    POST /api/llm/test         try one request's LLM settings (tiny completion)

Visitors may bring their own LLM key: the ``X-Archivist-LLM-Base|Model|Key``
request headers override the server's settings for that one request. The
server never stores or logs the key. Asks that spend the *server's* demo
key are capped per client (:data:`DEMO_QUOTA_DEFAULT` per hour; 429 past
the cap, with a hint to bring a key). ``/api/ask/stream`` streams the same
run as SSE frames and heartbeats while the queue is quiet, so long steps
never look dead through the tunnel.

The server is bounded: at most ``MAX_INFLIGHT`` requests are served at once,
so the console can face a tunnel (M5) without turning a burst of stalled
connections into unbounded threads on a 4.8 GB box.
"""

from __future__ import annotations

import ipaddress
import json
import os
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import pairwise
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from falkordb import Edge, FalkorDB, Node
from falkordb import Path as GraphPath
from redis.exceptions import ResponseError

from .accounts import (
    SESSION_COOKIE,
    AccountError,
    AccountStore,
    RateLimiter,
    User,
    clear_cookie,
    session_cookie,
)
from .actions import Actions
from .agent import Agent
from .llm import ChatLLM, LLMError
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
# /api/graph/memory (M4R P1): memory timeline caps. The console must stay
# renderable; every trim is reported in notes — same honesty rule as the rest.
MAX_MEMORY_EPISODES = 40
MAX_MEMORY_FACTS = 120
MAX_EPISODE_ANSWER = 400  # chars kept per episode answer in the timeline
# /api/graph/actions (inc1): the actions list is capped like memory, and the
# trim is reported in notes — never silently truncated.
MAX_ACTIONS = 120
_EPISODE_CORE_KEYS = frozenset(
    {"id", "kind", "source", "ts", "created_at", "question", "answer", "role", "run"}
)
# /api/graph/crew (M4R P1): crew runs — phase spine + handoff notes. Same
# honesty rule: every cap is reported in notes, and a run that never reached
# its verifier is marked interrupted — never implied complete.
MAX_CREW_RUNS = 20
MAX_CREW_ANSWER = 600  # chars kept per phase answer
MAX_CREW_INPUT = 1000  # chars kept per phase input (question prop)
#: Known crew phase order; unknown roles sort after these, by timestamp.
CREW_ROLE_ORDER = ("planner", "investigator", "verifier")
# /api/graph/history (M4R time machine): the repo's own past as UTC
# calendar-month buckets, plus an optional commit window. Buckets are only
# trimmed oldest-first past the cap (noted with the exact count); the
# window's commits and top files carry "N not shown" notes like every
# other projection on this box.
MAX_HISTORY_MONTHS = 600
MAX_HISTORY_COMMITS = 40
MAX_HISTORY_FILES = 12
# /api/graph/impact (M4R blast radius): the inverted call cone above one
# symbol. The walk itself is capped so a hub cannot turn one request into an
# unbounded path explosion; every trim — paths, symbols, files — is noted.
MAX_IMPACT_SYMBOLS = 150
MAX_IMPACT_FILES = 60
MAX_IMPACT_DEPTH = 6
# /api/graph/ownership (M4R ownership): who touched what. Ownership is
# inferred from diff-bearing commit history only — the graph holds no
# CODEOWNERS. Shares are exact over that history; every cap is reported.
MAX_OWNERS = 12
MAX_OWNERSHIP_FILES = 12
MAX_OWNERSHIP_RISKS = 12
MAX_OWNERSHIP_COMMITS = 12
MAX_HOT_FILE_OWNERS = 4
MAX_SUGGESTED_REVIEWERS = 3
OWNERSHIP_RECENT_DAYS = 365
OWNERSHIP_RISK_MIN_COMMITS = 6
OWNERSHIP_RISK_SHARE = 0.8
# /api/graph/induced (M4R step 3): per-step evidence ref caps, reported in
# notes when trimmed — the same honesty rule as every other projection.
MAX_INDUCED_STEPS = 12
MAX_STEP_SYMBOLS = 120
MAX_STEP_FILES = 80
MAX_STEP_MODULES = 40
MAX_STEP_COMMITS = 40
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

# BYO-key requests (M7): the browser keeps the visitor's LLM settings in
# localStorage and sends them per request. The server never stores or logs
# the key; it only forwards it to the endpoint the visitor named. Caps are
# generous for humans, useless for smuggling payloads.
LLM_HEADER_BASE = "X-Archivist-LLM-Base"
LLM_HEADER_MODEL = "X-Archivist-LLM-Model"
LLM_HEADER_KEY = "X-Archivist-LLM-Key"
MAX_LLM_BASE_LEN = 300
MAX_LLM_MODEL_LEN = 200
MAX_LLM_KEY_LEN = 400
# Demo-key ask quota (M7): the shared key is capped per client per hour;
# <= 0 disables the cap (dev boxes). BYO-key asks are not counted.
DEMO_QUOTA_DEFAULT = 40
DEMO_QUOTA_WINDOW = 3600.0
# "Test key" probe: one tiny completion, modest per-client rate.
LLM_TEST_LIMIT = 6
LLM_TEST_WINDOW = 600.0
# SSE streams (M7b): live asks. The handler heartbeats on a quiet queue so a
# long step never looks dead through the tunnel; when a client goes away the
# write fails, the abort flag is set, and the producer stops between steps.
STREAM_HEARTBEAT = 10.0

# Honest error codes per status: a refusal is not a "bad request", and a
# missing key is not a client error (REVIEW-M4a F5).
_ERROR_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    413: "too_large",
    415: "unsupported_media_type",
    429: "rate_limited",
    503: "unavailable",
}

#: Routes that never resolve a repo graph: auth must work even if a user's
#: registry is mid-ingest or a ``?repo=`` value is junk.
_AUTH_PATHS = frozenset(
    {"/api/auth/signup", "/api/auth/login", "/api/auth/logout", "/api/me"}
)

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
    """A clean 4xx for the JSON layer (``code`` overrides the status label)."""

    def __init__(self, status: int, message: str, *, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code


@dataclass(frozen=True)
class RequestContext:
    """What the HTTP layer knows before routing: who, from where, how."""

    user: User | None = None
    token: str | None = None
    headers: dict = field(default_factory=dict)
    client_ip: str = "unknown"
    secure: bool = False
    raw_body: bytes = b""


# One graph override and one response-header bag per request thread. The
# property trick keeps every existing ``self.graph`` call site unchanged
# while a request may only ever see the graph its session is allowed to.
_GRAPH_LOCAL = threading.local()
_CTX_LOCAL = threading.local()
_HEADERS_LOCAL = threading.local()


def _cookie_value(raw: str | None, name: str) -> str | None:
    """First ``name`` value from a Cookie header, tolerant of spacing."""
    for part in (raw or "").split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value or None
    return None


def _env_int(name: str, default: int) -> int:
    """Int env knob with a forgiving parse (junk falls back to the default)."""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _is_loopback_host(hostname: str) -> bool:
    """True for ``localhost`` and any loopback IP literal (127.0.0.0/8, ::1)."""
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _header_get(headers, name: str) -> str:
    """Case-insensitive single-header read over Message or plain mappings."""
    if not headers:
        return ""
    value = headers.get(name)
    if value:
        return str(value)
    try:
        items = headers.items()
    except AttributeError:
        return ""
    for key, candidate in items:
        if str(key).lower() == name.lower():
            return str(candidate or "")
    return ""


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


def scalar_rows(result) -> list[dict]:
    """Result rows as dicts keyed by their RETURN column names."""
    columns = [header[1] for header in result.header]
    return [dict(zip(columns, row, strict=True)) for row in result.result_set]


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


def _induced_refs(
    value, keys: tuple[str, ...], cap: int, label: str, notes: list[str]
) -> list[dict]:
    """Validate, dedupe and cap one ref list for ``/api/graph/induced``.

    Bad rows are skipped (counted in ``notes``); trimming is reported with
    the exact number not shown, same rule as every display cap on this box.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise ApiError(400, f"{label} refs must be a list")
    kept: list[dict] = []
    seen: set[tuple] = set()
    skipped = 0
    for row in value:
        if not isinstance(row, dict) or not all(
            isinstance(row.get(k), str) and row.get(k).strip() for k in keys
        ):
            skipped += 1
            continue
        key = tuple(row[k] for k in keys)
        if key in seen:
            continue
        seen.add(key)
        kept.append({k: row[k] for k in keys})
    if skipped:
        notes.append(f"{skipped} malformed {label} ref(s) skipped")
    if len(kept) > cap:
        notes.append(f"{label} refs capped at {cap} ({len(kept) - cap} not shown)")
        kept = kept[:cap]
    return kept


def _is_bot_name(name: str | None) -> bool:
    """Name-only bot heuristic: ``[bot]`` suffix (GitHub Apps) or *BotApp*.

    The graph has no account-type data, so this can only flag the naming
    convention common on GitHub. Bots stay in the numbers — hiding them
    would make the shares disagree with the commit count — but the payload
    marks them so the console can draw them differently.
    """
    lowered = (name or "").lower()
    return lowered.endswith(("[bot]", "botapp"))


def _utc_date(ts) -> str:
    """Epoch seconds as a UTC calendar date (for human-readable notes)."""
    if not ts:
        return "never"
    return datetime.fromtimestamp(int(ts), UTC).strftime("%Y-%m-%d")


def _is_test_path(path: str) -> bool:
    """Path-only test heuristic: test dirs or ``test_*.py`` / ``*_test.py``.

    The graph carries no test-runner edges, so this can only say where a
    file usually lives. The payload discloses the heuristic in ``notes``
    rather than mistaking it for runtime evidence.
    """
    parts = path.replace("\\", "/").lower().split("/")
    name = parts[-1]
    return (
        any(
            part in {"test", "tests", "testing", "spec", "specs"} for part in parts[:-1]
        )
        or name.startswith("test_")
        or name.endswith(("_test.py", "_test.ts", ".test.ts", ".test.tsx"))
    )


def _month_buckets(rows) -> list[dict]:
    """Fold per-commit history rows (seq, ts, touches, added, removed) into
    UTC calendar months — oldest first, quiet months included with zeros.

    Calendar math lives here, not in Cypher: the endpoint promises real
    months (``2010-04`` …), and ``ts / 2592000`` would drift into 30-day
    blocks. Empty months are kept as zero buckets so the timeline is
    continuous (no month silently disappears) and adjacent buckets tile
    exactly — each carries its ``[t0, t1)`` epoch bounds, so a scrubber can
    ask for the commits inside one bucket without re-deriving the month
    client-side.
    """
    buckets: dict[tuple[int, int], dict] = {}
    for _seq, ts, touches, added, removed in rows:
        dt = datetime.fromtimestamp(ts, tz=UTC)
        bucket = buckets.get((dt.year, dt.month))
        if bucket is None:
            year, month = dt.year, dt.month
            next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
            bucket = {
                "label": f"{year:04d}-{month:02d}",
                "t0": int(datetime(year, month, 1, tzinfo=UTC).timestamp()),
                "t1": int(datetime(next_year, next_month, 1, tzinfo=UTC).timestamp()),
                "commits": 0,
                "touches": 0,
                "added": 0,
                "removed": 0,
            }
            buckets[(year, month)] = bucket
        bucket["commits"] += 1
        bucket["touches"] += int(touches or 0)
        bucket["added"] += int(added or 0)
        bucket["removed"] += int(removed or 0)
    if not buckets:
        return []

    def next_month(year: int, month: int) -> tuple[int, int]:
        return (year + 1, 1) if month == 12 else (year, month + 1)

    filled: list[dict] = []
    year, month = min(buckets)
    last = max(buckets)
    while (year, month) <= last:
        bucket = buckets.get((year, month))
        if bucket is None:
            next_year, next_month_no = next_month(year, month)
            bucket = {
                "label": f"{year:04d}-{month:02d}",
                "t0": int(datetime(year, month, 1, tzinfo=UTC).timestamp()),
                "t1": int(
                    datetime(next_year, next_month_no, 1, tzinfo=UTC).timestamp()
                ),
                "commits": 0,
                "touches": 0,
                "added": 0,
                "removed": 0,
            }
        filled.append(bucket)
        year, month = next_month(year, month)
    return filled


_FINAL = object()  # queue sentinel: the producer thread has finished


def _sse_frame(event: str, data: dict) -> bytes:
    """One Server-Sent Event: named event + JSON data + blank-line terminator."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n".encode()


class StreamResponse:
    """A streaming (SSE) response: a queue of byte frames + an abort flag.

    The producer runs on its own daemon thread: it executes the ask and puts
    pre-framed SSE bytes on the queue (unbounded — the handler drains it as
    fast as the socket allows). The HTTP handler writes whatever arrives and
    emits ``: ping`` heartbeats when the queue stays quiet. If a write fails
    the handler sets :attr:`abort`; the producer polls it between steps and
    winds down cooperatively — its cleanup (episode bookkeeping, connections)
    still runs, so an abandoned stream never leaves a half-written record.
    """

    def __init__(self, producer, *, heartbeat: float = STREAM_HEARTBEAT):
        self.status = 200
        self.content_type = "text/event-stream; charset=utf-8"
        self.heartbeat = heartbeat
        self.abort = threading.Event()
        self.queue: queue.Queue = queue.Queue()
        self._producer = producer
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        try:
            self._producer(self.abort, self.queue)
        except Exception as exc:  # noqa: BLE001 - the frame is the contract
            self.queue.put(
                _sse_frame("error", {"message": f"{type(exc).__name__}: {exc}"[:500]})
            )
        finally:
            self.queue.put(_FINAL)


@dataclass
class _AskSetup:
    """Validated request state shared by /api/ask and /api/ask/stream.

    Everything the run needs is resolved while the request thread still owns
    its context (graph resolution, BYO overrides, quota gate); the streaming
    producer thread then works from plain captured values only.
    """

    question: str
    max_steps: int
    remember: bool
    warnings: list[str]
    overrides: dict
    llm: object
    graph: str


class WebApp:
    """Request routing over one repo graph. One instance per server."""

    def __init__(
        self,
        graph: str = "repo:flask",
        host: str = "127.0.0.1",
        port: int = 6379,
        llm_factory=None,
        *,
        accounts: AccountStore | None = None,
        demo_graph: str | None = None,
        signup: str = "open",
        demo_quota: int | None = None,
        clock: Callable[[], float] | None = None,
    ):
        self.host = host
        self.port = port
        # The default graph is what anonymous visitors and signed-in users
        # without a repo of their own see. ``demo_graph`` names the shared
        # fallback explicitly (it is also how memory writes stay gated).
        self._default_graph = graph
        self._demo_graph = demo_graph or graph
        self.accounts = accounts
        self.signup = "open" if signup == "open" else "closed"
        # Injection seam for tests and the scripted capture server
        # (``serve.py --llm-script``); production callers get the real
        # BYO-key client. The agent loop, tools and graph stay real either
        # way — only the model's next-move choices are canned.
        self._llm_factory = llm_factory or ChatLLM
        # Demo-key ask quota + key-test rate limit (M7): per-process state
        # with an injectable clock, same rationale as accounts.RateLimiter.
        if demo_quota is None:
            demo_quota = _env_int("ARCHIVIST_DEMO_QUOTA", DEMO_QUOTA_DEFAULT)
        self.demo_quota = max(0, int(demo_quota))
        self._limiter_clock = clock or time.time
        self._ask_limiter = RateLimiter(self._limiter_clock)
        self._llm_test_limiter = RateLimiter(self._limiter_clock)

    @property
    def graph(self) -> str:
        """The graph this request may read — resolved per session, never raw."""
        return getattr(_GRAPH_LOCAL, "graph", None) or self._default_graph

    @property
    def demo_graph(self) -> str:
        return self._demo_graph

    # -- per-request state -------------------------------------------------

    def reset_headers(self) -> None:
        _HEADERS_LOCAL.items = []

    def take_headers(self) -> list[tuple[str, str]]:
        items = getattr(_HEADERS_LOCAL, "items", [])
        _HEADERS_LOCAL.items = []
        return items

    def _add_header(self, name: str, value: str) -> None:
        items = getattr(_HEADERS_LOCAL, "items", None)
        if items is None:
            items = _HEADERS_LOCAL.items = []
        items.append((name, value))

    def _enter(self, ctx: RequestContext, path: str, query: dict) -> None:
        """Bind request identity + allowed graph to this thread."""
        _CTX_LOCAL.ctx = ctx
        _GRAPH_LOCAL.graph = self._default_graph
        if path in _AUTH_PATHS or self.accounts is None:
            return
        values = query.get("repo") or []
        repo_id = values[0] if values and isinstance(values[0], str) else None
        if repo_id is not None and (not repo_id or len(repo_id) > 64):
            raise ApiError(404, "no such repository", code="no_such_repo")
        try:
            graph, _repo = self.accounts.resolve_graph(
                ctx.user, repo_id, demo_graph=self._demo_graph
            )
        except AccountError as exc:
            raise ApiError(exc.status, exc.message, code=exc.code) from exc
        _GRAPH_LOCAL.graph = graph

    def _leave(self) -> None:
        _GRAPH_LOCAL.graph = None
        _CTX_LOCAL.ctx = None

    @property
    def _request_user(self) -> User | None:
        ctx = getattr(_CTX_LOCAL, "ctx", None)
        return ctx.user if ctx else None

    @property
    def _request_ctx(self) -> RequestContext:
        return getattr(_CTX_LOCAL, "ctx", None) or RequestContext()

    # -- auth --------------------------------------------------------------

    def _require_accounts(self) -> AccountStore:
        if self.accounts is None:
            raise ApiError(
                503, "accounts are not enabled on this server", code="accounts_disabled"
            )
        return self.accounts

    def _auth_guard(self, ctx: RequestContext) -> None:
        """JSON-only + same-origin mutations (cheap CSRF gate for cookies)."""
        self._require_accounts()
        ctype = (ctx.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            raise ApiError(
                415,
                "auth requests must be JSON (Content-Type: application/json)",
                code="json_required",
            )
        origin = (ctx.headers.get("Origin") or "").strip()
        if origin:
            host = (ctx.headers.get("Host") or "").strip().lower()
            if not host or urlsplit(origin).netloc.lower() != host:
                raise ApiError(403, "cross-origin request refused", code="bad_origin")

    @staticmethod
    def _client_key(ctx: RequestContext) -> str:
        """Rate-limit key: Cloudflare's client ip when present, else peer ip.

        Trust assumption: the direct port is loopback-only; through the
        tunnel Cloudflare overwrites this header. A publicly exposed port
        would make it spoofable — documented in docs/DEPLOY.md.
        """
        forwarded = (ctx.headers.get("CF-Connecting-IP") or "").strip()
        return forwarded or ctx.client_ip or "unknown"

    def _open_session(self, user: User, ctx: RequestContext) -> dict:
        accounts = self._require_accounts()
        token, expires = accounts.open_session(user)
        max_age = max(0, expires - accounts.now())
        self._add_header(
            "Set-Cookie", session_cookie(token, secure=ctx.secure, max_age=max_age)
        )
        return {"user": user.public(), "repos": accounts.repos(user.uid)}

    def _signup(self, body: dict, ctx: RequestContext) -> dict:
        self._auth_guard(ctx)
        if self.signup != "open":
            raise ApiError(
                403, "signups are closed on this server", code="signup_closed"
            )
        try:
            user = self.accounts.signup(
                body.get("username"), body.get("password"), ip=self._client_key(ctx)
            )
        except AccountError as exc:
            raise ApiError(exc.status, exc.message, code=exc.code) from exc
        return self._open_session(user, ctx)

    def _login(self, body: dict, ctx: RequestContext) -> dict:
        self._auth_guard(ctx)
        try:
            user = self.accounts.authenticate(
                body.get("username"), body.get("password"), ip=self._client_key(ctx)
            )
        except AccountError as exc:
            raise ApiError(exc.status, exc.message, code=exc.code) from exc
        return self._open_session(user, ctx)

    def _logout(self, ctx: RequestContext) -> dict:
        self._auth_guard(ctx)
        self.accounts.close_session(ctx.token)
        self._add_header("Set-Cookie", clear_cookie(secure=ctx.secure))
        return {"ok": True}

    def _me(self, ctx: RequestContext) -> dict:
        user = ctx.user
        repos = self.accounts.repos(user.uid) if (self.accounts and user) else []
        return {
            "user": user.public() if user else None,
            "anonymous": user is None,
            "signup": self.signup if self.accounts else "closed",
            "repos": repos,
            "demo_graph": self._demo_graph,
            "graph": self.graph,
        }

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

    def _scalar(self, cypher: str, params: dict | None = None):
        """First cell of a scalar query (counts, window min/max); None if empty."""
        rows = self._ro(cypher, params).result_set
        return rows[0][0] if rows and rows[0] else None

    @staticmethod
    def _json(status: int, payload: dict) -> tuple[int, str, bytes]:
        body = json.dumps(payload, default=str).encode("utf-8")
        return status, "application/json; charset=utf-8", body

    # -- dispatch ----------------------------------------------------------

    def dispatch(
        self,
        method: str,
        path: str,
        query: dict,
        body: dict,
        ctx: RequestContext | None = None,
    ) -> tuple[int, str, bytes] | StreamResponse:
        ctx = ctx or RequestContext()
        try:
            self._enter(ctx, path, query)
            if method == "GET":
                if path in ("/", "/classic", "/classic/"):
                    # "/" is 302'd to /console/ at the HTTP layer; /classic/
                    # keeps the original v1 shell reachable for reference.
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
                if path == "/api/graph/memory":
                    return self._json(200, self._graph_memory(query))
                if path == "/api/graph/actions":
                    return self._json(200, self._graph_actions(query))
                if path == "/api/graph/crew":
                    return self._json(200, self._graph_crew(query))
                if path == "/api/graph/history":
                    return self._json(200, self._graph_history(query))
                if path == "/api/graph/impact":
                    return self._json(200, self._graph_impact(query))
                if path == "/api/graph/ownership":
                    return self._json(200, self._graph_ownership(query))
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
                if path == "/api/me":
                    return self._json(200, self._me(ctx))
            elif method == "POST":
                if path == "/github/webhook":
                    return self._github_webhook(ctx)
                if path == "/slack/events":
                    return self._slack_events(ctx)
                if path == "/api/auth/signup":
                    return self._json(200, self._signup(body, ctx))
                if path == "/api/auth/login":
                    return self._json(200, self._login(body, ctx))
                if path == "/api/auth/logout":
                    return self._json(200, self._logout(ctx))
                if path == "/api/graph/induced":
                    return self._json(200, self._graph_induced(body))
                if path.startswith("/api/actions/") and path.endswith("/complete"):
                    return self._json(
                        200, self._complete_action(path, body, ctx)
                    )
                if path == "/api/cypher":
                    return self._json(200, self._cypher(body))
                if path == "/api/ask":
                    return self._json(200, self._ask(body))
                if path == "/api/ask/stream":
                    return self._ask_stream(body)
                if path == "/api/llm/test":
                    return self._json(200, self._llm_test(ctx))
            return self._json(
                404, {"error": "not_found", "message": f"no route {method} {path}"}
            )
        except ApiError as exc:
            return self._json(
                exc.status,
                {
                    "error": exc.code or _ERROR_CODES.get(exc.status, "error"),
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
        finally:
            self._leave()

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

        The containment gate canonicalizes its base first: a symlinked
        ``console/dist`` (shared build dirs, stow-style setups) must serve,
        while a symlink *inside* dist pointing outside is still refused
        (REVIEW-M4R B1 W1).
        """
        rel = rel.lstrip("/") or "index.html"
        base = CONSOLE_DIR.resolve()
        target = (base / rel).resolve()
        if not target.is_relative_to(base):
            raise ApiError(404, "no such console asset")
        index = base / "index.html"
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
        llm = self._new_llm({})
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

    def _graph_memory(self, query: dict) -> dict:
        """Memory timeline (Console v2 P1): episodes + bi-temporal facts.

        Three views over the same state, mirroring the ``recall`` tool:
        default is "true now" (open windows); ``history=1`` returns every
        fact with its windows intact (invalidate-don't-delete); and
        ``as_of=<epoch>`` answers "what was true at T" (event time) — the
        console scrubber's query. ``chain_facts`` carries the minimal
        fields of supersede-chain neighbours the current view hides, so a
        chain chip can name the older claim without a second fetch. Every
        cap is reported in ``notes`` with exact counts.
        """
        raw_as_of = (query.get("as_of") or [""])[0].strip()
        as_of = None
        if raw_as_of:
            try:
                as_of = int(raw_as_of)
            except ValueError:
                as_of = None
            if as_of is None or as_of < 0:
                raise ApiError(
                    400, "as_of must be epoch seconds (e.g. ?as_of=1792111642)"
                )
        history = (query.get("history") or ["0"])[0].strip().lower() in (
            "1",
            "true",
            "yes",
        )
        mode = "as_of" if as_of is not None else ("history" if history else "now")

        counts = scalar_rows(self._ro("""
                MATCH (e:Episode)
                RETURN count(e) AS episodes,
                       min(coalesce(e.ts, e.created_at)) AS earliest,
                       max(coalesce(e.ts, e.created_at)) AS latest
                """))
        counts = (
            counts[0] if counts else {"episodes": 0, "earliest": None, "latest": None}
        )
        facts_total = self._scalar("MATCH (f:Fact) RETURN count(f)") or 0
        open_facts = (
            self._scalar("MATCH (f:Fact) WHERE f.invalid_at IS NULL RETURN count(f)")
            or 0
        )
        handoff_edges = self._scalar("MATCH ()-[h:HANDOFF]->() RETURN count(h)") or 0
        supersede_edges = (
            self._scalar("MATCH (:Fact)-[s:SUPERSEDES]->(:Fact) RETURN count(s)") or 0
        )

        ep_params: dict = {"limit": MAX_MEMORY_EPISODES}
        ep_where = ""
        if as_of is not None:
            ep_where = "WHERE coalesce(e.ts, e.created_at) <= $as_of"
            ep_params["as_of"] = as_of
        episode_rows = scalar_rows(
            self._ro(
                f"""
                MATCH (e:Episode)
                {ep_where}
                RETURN e.id AS id, e.kind AS kind, e.source AS source,
                       coalesce(e.ts, e.created_at) AS ts,
                       e.created_at AS created_at, properties(e) AS props
                ORDER BY coalesce(e.ts, e.created_at) DESC, e.id
                LIMIT $limit
                """,
                ep_params,
            )
        )

        fact_params: dict = {"limit": MAX_MEMORY_FACTS}
        if as_of is not None:
            fact_where = (
                "WHERE coalesce(f.valid_at, f.created_at) <= $as_of"
                " AND (f.invalid_at IS NULL OR f.invalid_at > $as_of)"
            )
            fact_params["as_of"] = as_of
            facts_count = (
                self._scalar(
                    "MATCH (f:Fact)"
                    " WHERE coalesce(f.valid_at, f.created_at) <= $as_of"
                    " AND (f.invalid_at IS NULL OR f.invalid_at > $as_of)"
                    " RETURN count(f)",
                    {"as_of": as_of},
                )
                or 0
            )
        elif mode == "history":
            fact_where = ""
            facts_count = facts_total
        else:
            fact_where = "WHERE f.invalid_at IS NULL"
            facts_count = open_facts
        fact_rows = scalar_rows(
            self._ro(
                f"""
                MATCH (f:Fact) {fact_where}
                OPTIONAL MATCH (e:Episode)-[:PRODUCED]->(f)
                RETURN f.id AS id, f.text AS text, f.kind AS kind,
                       f.valid_at AS valid_at, f.invalid_at AS invalid_at,
                       f.created_at AS created_at, f.expired_at AS expired_at,
                       collect(DISTINCT e.id) AS episode_ids
                ORDER BY coalesce(f.valid_at, f.created_at) DESC, f.id
                LIMIT $limit
                """,
                fact_params,
            )
        )

        pairs = scalar_rows(
            self._ro(
                "MATCH (a:Fact)-[:SUPERSEDES]->(b:Fact)"
                " RETURN a.id AS new_id, b.id AS old_id"
            )
        )
        # Minimal fields for every fact: a chain can reach facts the current
        # view hides (a superseded claim in "now" mode), and the timeline
        # should still be able to name them. Memory graphs stay small by design.
        all_minimal = scalar_rows(self._ro("""
                MATCH (f:Fact)
                RETURN f.id AS id, f.text AS text, f.kind AS kind,
                       f.valid_at AS valid_at, f.invalid_at AS invalid_at
                """))
        ref_rows = scalar_rows(self._ro("""
                MATCH (f:Fact)-[:ABOUT]->(x)
                RETURN f.id AS fact_id, labels(x) AS labels,
                       x.path AS path, x.file AS file, x.qualname AS qualname,
                       x.sha AS sha
                """))
        handoff_rows = scalar_rows(self._ro("""
                MATCH (a:Episode)-[h:HANDOFF]->(b:Episode)
                RETURN a.id AS from_id, b.id AS to_id, h.note AS note,
                       h.created_at AS ts
                """))

        refs_by_fact: dict[str, list] = {}
        for row in ref_rows:
            labels = row.get("labels") or []
            kind = str(labels[0]) if labels else "Node"
            if row.get("path"):
                label = row["path"]
            elif row.get("qualname"):
                label = f"{row.get('file')}:{row['qualname']}"
            elif row.get("sha"):
                label = str(row["sha"])[:10]
            else:
                label = None
            refs_by_fact.setdefault(row["fact_id"], []).append(
                {"kind": kind, "label": label}
            )

        out_handoffs: dict[str, list] = {}
        in_handoffs: dict[str, list] = {}
        for h in handoff_rows:
            item = {"note": h["note"], "ts": h["ts"]}
            out_handoffs.setdefault(h["from_id"], []).append({"to": h["to_id"], **item})
            in_handoffs.setdefault(h["to_id"], []).append(
                {"from": h["from_id"], **item}
            )

        episodes_out: list[dict] = []
        truncated = 0
        for row in episode_rows:
            props = dict(row.pop("props") or {})
            answer = props.get("answer")
            if isinstance(answer, str) and len(answer) > MAX_EPISODE_ANSWER:
                answer = answer[: MAX_EPISODE_ANSWER - 1] + "…"
                truncated += 1
            episodes_out.append(
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "source": row["source"],
                    "ts": row["ts"],
                    "created_at": row["created_at"],
                    "question": props.get("question"),
                    "answer": answer,
                    "role": props.get("role"),
                    "run": props.get("run"),
                    "props": {
                        key: _jsonable(value)
                        for key, value in props.items()
                        if key not in _EPISODE_CORE_KEYS and value is not None
                    },
                    "handoffs": sorted(
                        out_handoffs.get(row["id"], []),
                        key=lambda h: h["ts"] or 0,
                    ),
                    "handoff_in": sorted(
                        in_handoffs.get(row["id"], []),
                        key=lambda h: h["ts"] or 0,
                    ),
                }
            )

        facts_out: list[dict] = []
        for row in fact_rows:
            episode_ids = [e for e in (row["episode_ids"] or []) if e]
            facts_out.append(
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "text": row["text"],
                    "valid_at": row["valid_at"],
                    "invalid_at": row["invalid_at"],
                    "created_at": row["created_at"],
                    "expired_at": row["expired_at"],
                    "episode_id": episode_ids[0] if episode_ids else None,
                    "supersedes": [],
                    "superseded_by": [],
                    "refs": refs_by_fact.get(row["id"], []),
                }
            )
        by_id = {f["id"]: f for f in facts_out}
        for pair in pairs:
            if pair["new_id"] in by_id:
                by_id[pair["new_id"]]["supersedes"].append(pair["old_id"])
            if pair["old_id"] in by_id:
                by_id[pair["old_id"]]["superseded_by"].append(pair["new_id"])
        linked = {fid for pair in pairs for fid in (pair["new_id"], pair["old_id"])}
        chain_facts = [
            fact
            for fact in all_minimal
            if fact["id"] in linked and fact["id"] not in by_id
        ]

        notes: list[str] = []
        if facts_count > MAX_MEMORY_FACTS:
            notes.append(
                f"facts capped at {MAX_MEMORY_FACTS} "
                f"({facts_count - MAX_MEMORY_FACTS} not shown)"
            )
        if counts["episodes"] > MAX_MEMORY_EPISODES:
            notes.append(
                f"episodes capped at {MAX_MEMORY_EPISODES} "
                f"({counts['episodes'] - MAX_MEMORY_EPISODES} not shown, newest first)"
            )
        if mode == "history":
            notes.append("invalidated facts included (full history)")
        if mode == "as_of":
            notes.append(f"as of {as_of} (event time)")
        if truncated:
            notes.append(f"{truncated} episode answer(s) truncated for display")
        if not facts_out and not episodes_out:
            notes.append(
                f"nothing true as of {as_of}"
                if as_of is not None
                else "no memory recorded yet"
            )

        return {
            "graph": self.graph,
            "mode": mode,
            "as_of": as_of,
            "range": {"earliest": counts["earliest"], "latest": counts["latest"]},
            "counts": {
                "episodes": counts["episodes"],
                "facts": facts_total,
                "open_facts": open_facts,
                "handoff_edges": handoff_edges,
                "supersede_edges": supersede_edges,
            },
            "episodes": episodes_out,
            "facts": facts_out,
            "chain_facts": chain_facts,
            "notes": notes,
        }

    def _graph_actions(self, query: dict) -> dict:
        """Actions (inc1): the agent's recorded next steps, proposed first.

        Mirrors ``/api/graph/memory``: a ``?status=`` filter (``proposed`` /
        ``done`` / ``superseded`` / ``all``, default ``all``), an
        ``actions`` list joined to ``ABOUT`` targets and the producing
        episode, and a ``counts`` summary. Every cap is reported in
        ``notes`` — never silently truncated.
        """
        raw_status = (query.get("status") or ["all"])[0].strip().lower()
        status = raw_status if raw_status in ("proposed", "done", "superseded", "all") else None
        actions = Actions(graph=self.graph, host=self.host, port=self.port)
        try:
            payload = actions.list_actions(status=status)
        finally:
            actions.close()
        actions_out = payload["actions"]
        counts = payload["counts"]
        total = counts["total"]
        if total > MAX_ACTIONS:
            actions_out = actions_out[:MAX_ACTIONS]
        notes: list[str] = []
        if status is None and raw_status:
            notes.append(
                f"unknown status {raw_status!r} — showing all actions"
            )
        if total > MAX_ACTIONS:
            notes.append(
                f"actions capped at {MAX_ACTIONS} ({total - MAX_ACTIONS} not shown)"
            )
        if not actions_out:
            notes.append("no actions recorded yet")
        return {
            "graph": self.graph,
            "status": status,
            "counts": counts,
            "actions": actions_out,
            "notes": notes,
        }

    def _complete_action(self, path: str, body: dict, ctx: RequestContext) -> dict:
        """Close one action (``done``/``superseded``), auth-gated like writes.

        Closing mutates shared state, so it requires an authenticated user
        with their own repository — the demo graph is shared and refused.
        """
        self._auth_guard(ctx)
        if self._request_user is None:
            raise ApiError(401, "sign in to complete actions", code="unauthorized")
        if self.graph == self._demo_graph:
            raise ApiError(
                403,
                "actions live in your own repository — the demo graph is shared",
                code="shared_graph",
            )
        action_id = path[len("/api/actions/") : -len("/complete")].strip()
        if not action_id:
            raise ApiError(400, "action id is required", code="bad_request")
        status = str(body.get("status") or "done").strip().lower()
        if status not in ("done", "superseded"):
            raise ApiError(400, "status must be 'done' or 'superseded'")
        actions = Actions(graph=self.graph, host=self.host, port=self.port)
        try:
            result = actions.complete_action(action_id, status=status)
        finally:
            actions.close()
        return result

    def _graph_crew(self, query: dict) -> dict:
        """Crew runs (Console v2 P1): a phase spine per run + handoff notes.

        One row per phase episode (``role`` + ``run`` props), grouped into
        runs newest-first. The spine is ordered by the known crew order
        (planner → investigator → verifier, timestamp tie-broken), so a run
        reads as the sequence it was. Handoff notes are queried back from
        ``(:Episode)-[:HANDOFF]->(:Episode)`` edges — the crew's coordination
        record is graph state, not a log file.

        Honesty rules: runs beyond the cap, truncated inputs/answers, phase
        episodes without a run id, and runs whose spine is missing expected
        handoff edges (degraded bookkeeping) are all reported in ``notes``;
        a run that never reached its verifier is ``interrupted``, never
        implied complete.
        """
        rows = scalar_rows(self._ro("""
                MATCH (e:Episode)
                WHERE e.role IS NOT NULL
                RETURN e.run AS run, e.id AS id, e.role AS role,
                       coalesce(e.ts, e.created_at) AS ts,
                       e.question AS question, e.answer AS answer,
                       e.duration_ms AS duration_ms, e.steps AS steps,
                       e.queries AS queries, e.llm_calls AS llm_calls,
                       e.partial AS partial, e.error AS error
                ORDER BY coalesce(e.ts, e.created_at) DESC, e.id
                """))
        handoff_rows = scalar_rows(self._ro("""
                MATCH (a:Episode)-[h:HANDOFF]->(b:Episode)
                WHERE a.run IS NOT NULL AND a.run = b.run
                  AND a.role IS NOT NULL AND b.role IS NOT NULL
                RETURN a.run AS run, a.role AS from_role, b.role AS to_role,
                       h.note AS note, h.created_at AS ts
                """))

        by_run: dict[str, list[dict]] = {}
        skipped = 0
        for row in rows:
            if row.get("run"):
                by_run.setdefault(str(row["run"]), []).append(row)
            else:
                skipped += 1
        run_handoffs: dict[str, list[dict]] = {}
        for h in handoff_rows:
            run_handoffs.setdefault(str(h["run"]), []).append(h)

        def role_index(role: object) -> int:
            try:
                return CREW_ROLE_ORDER.index(str(role))
            except ValueError:
                return len(CREW_ROLE_ORDER)

        notes: list[str] = []
        truncated_inputs = truncated_answers = 0
        runs_out: list[dict] = []
        for run_id, phases in by_run.items():
            phases.sort(
                key=lambda p: (p["ts"] or 0, role_index(p["role"]), str(p["id"]))
            )
            phases_out: list[dict] = []
            for p in phases:
                question, answer = p["question"], p["answer"]
                cut_in = isinstance(question, str) and len(question) > MAX_CREW_INPUT
                if cut_in:
                    question = question[: MAX_CREW_INPUT - 1] + "…"
                    truncated_inputs += 1
                cut_ans = isinstance(answer, str) and len(answer) > MAX_CREW_ANSWER
                if cut_ans:
                    answer = answer[: MAX_CREW_ANSWER - 1] + "…"
                    truncated_answers += 1
                phases_out.append(
                    {
                        "id": p["id"],
                        "role": p["role"],
                        "ts": p["ts"],
                        "question": question,
                        "input_truncated": cut_in,
                        "answer": answer,
                        "answer_truncated": cut_ans,
                        "duration_ms": p["duration_ms"],
                        "steps": p["steps"],
                        "queries": p["queries"],
                        "llm_calls": p["llm_calls"],
                        "partial": p["partial"],
                        "error": p["error"],
                    }
                )

            roles_present = {str(p["role"]) for p in phases_out}
            has_error = any(p["error"] for p in phases_out)
            if not set(CREW_ROLE_ORDER).issubset(roles_present) or has_error:
                status = "interrupted"
            elif any(p["partial"] for p in phases_out):
                status = "partial"
            else:
                status = "complete"

            handoffs_sorted = sorted(
                run_handoffs.get(run_id, []),
                key=lambda h: (
                    h["ts"] or 0,
                    role_index(h["from_role"]),
                    role_index(h["to_role"]),
                ),
            )
            expected_edges = sum(
                1
                for a_phase, b_phase in pairwise(phases_out)
                if role_index(b_phase["role"]) == role_index(a_phase["role"]) + 1
            )
            if expected_edges > len(handoffs_sorted):
                notes.append(
                    f"{run_id}: {expected_edges} handoff edge(s) expected, "
                    f"{len(handoffs_sorted)} found (bookkeeping may have degraded)"
                )

            anchor = next(
                (p for p in phases_out if str(p["role"]) == "planner"),
                phases_out[0] if phases_out else None,
            )
            stamps = [p["ts"] for p in phases_out if p["ts"] is not None]
            runs_out.append(
                {
                    "run": run_id,
                    "question": (anchor or {}).get("question"),
                    "started_at": min(stamps, default=None),
                    "ended_at": max(stamps, default=None),
                    "status": status,
                    "phases": phases_out,
                    "handoffs": [
                        {
                            "from": h["from_role"],
                            "to": h["to_role"],
                            "note": h["note"],
                            "ts": h["ts"],
                        }
                        for h in handoffs_sorted
                    ],
                }
            )

        runs_out.sort(key=lambda r: (r["ended_at"] or 0, r["run"]), reverse=True)
        total_runs = len(runs_out)
        if total_runs > MAX_CREW_RUNS:
            notes.append(
                f"runs capped at {MAX_CREW_RUNS} "
                f"({total_runs - MAX_CREW_RUNS} not shown, newest first)"
            )
            runs_out = runs_out[:MAX_CREW_RUNS]
        if skipped:
            notes.append(f"{skipped} phase episode(s) without a run id skipped")
        if truncated_inputs:
            notes.append(f"{truncated_inputs} phase input(s) truncated for display")
        if truncated_answers:
            notes.append(f"{truncated_answers} phase answer(s) truncated for display")
        if not total_runs:
            notes.append("no crew runs recorded yet")

        return {
            "graph": self.graph,
            "counts": {
                "runs": total_runs,
                "phases": len(rows) - skipped,
                "handoffs": len(handoff_rows),
            },
            "runs": runs_out,
            "notes": notes,
        }

    def _graph_history(self, query: dict) -> dict:
        """Time machine (M4R): the repo's past as UTC calendar-month buckets.

        Scope: the whole repo, or one file (``?file=``) — file scope is how
        the console replays a single file's (or symbol's file's) evolution.
        ``?from=&to=`` (epoch seconds, ``[from, to)``) adds the window
        section: its commits (newest first, capped) and, repo-wide, its top
        files by churn.

        Honesty rules that shaped the queries: merge commits carry no diff
        and are *kept* in repo scope (0 touches — never dropped); binary
        diffs count 0 lines (git numstat, not us); a file-scoped window
        row carries that file's diff only — the commit may have touched
        others, and the response does not pretend otherwise. Every cap is
        reported in ``notes`` with exact counts.
        """
        file = (query.get("file") or [None])[0] or None
        frm_raw, to_raw = (
            (query.get("from") or [None])[0],
            (query.get("to") or [None])[0],
        )
        if (frm_raw is None) != (to_raw is None):
            raise ApiError(400, "pass both ?from= and ?to= (epoch seconds)")
        window = None
        if frm_raw is not None:
            try:
                window = (int(frm_raw), int(to_raw))
            except (TypeError, ValueError):
                raise ApiError(400, "from/to must be epoch seconds (integers)")
            if window[0] >= window[1]:
                raise ApiError(400, "from must be < to")

        notes: list[str] = []
        if file and not self._scalar(
            "MATCH (f:File {path: $path}) RETURN count(f) AS n", {"path": file}
        ):
            raise ApiError(404, f"no File `{file}` in {self.graph}")

        if file:
            rows = self._ro(
                """
                MATCH (c:Commit)-[m:MODIFIED]->(f:File {path: $path})
                RETURN c.seq AS seq, c.ts AS ts, 1 AS touches,
                       coalesce(m.added, 0) AS added,
                       coalesce(m.removed, 0) AS removed
                ORDER BY c.seq DESC
                """,
                {"path": file},
            ).result_set
        else:
            rows = self._ro("""
                MATCH (c:Commit)
                OPTIONAL MATCH (c)-[m:MODIFIED]->(f:File)
                RETURN c.seq AS seq, c.ts AS ts, count(DISTINCT f) AS touches,
                       sum(coalesce(m.added, 0)) AS added,
                       sum(coalesce(m.removed, 0)) AS removed
                ORDER BY c.seq DESC
                """).result_set

        buckets = _month_buckets(rows)
        if len(buckets) > MAX_HISTORY_MONTHS:
            dropped = len(buckets) - MAX_HISTORY_MONTHS
            buckets = buckets[-MAX_HISTORY_MONTHS:]
            notes.append(
                f"buckets capped at {MAX_HISTORY_MONTHS} months "
                f"({dropped} older months not shown)"
            )
        notes.append(
            "buckets are UTC calendar months (quiet months included with "
            "zeros); merge commits carry no diff (kept with 0 touches); "
            "binary diffs count 0 lines"
        )
        if not rows:
            notes.append(
                f"no commits touch `{file}` in this graph"
                if file
                else "no commits in this graph"
            )

        if file:
            files_touched = 1 if rows else 0
        else:
            files_touched = (
                self._scalar(
                    "MATCH (:Commit)-[:MODIFIED]->(f:File) "
                    "RETURN count(DISTINCT f) AS n"
                )
                or 0
            )
        payload = {
            "scope": {"file": file},
            "buckets": buckets,
            "totals": {
                "commits": len(rows),
                "added": sum(int(r[3] or 0) for r in rows),
                "removed": sum(int(r[4] or 0) for r in rows),
                "files": files_touched,
                "first_ts": min((r[1] for r in rows), default=None),
                "last_ts": max((r[1] for r in rows), default=None),
            },
            "notes": notes,
        }
        if window is not None:
            payload["window"] = self._history_window(file, window, notes)
        return payload

    def _history_window(self, file: str | None, window: tuple, notes: list) -> dict:
        """The commits (and repo-scope top files) inside one [from, to) window."""
        frm, to = window
        params = {"from": frm, "to": to}
        if file:
            rows = self._ro(
                f"""
                MATCH (c:Commit)-[m:MODIFIED]->(f:File {{path: $path}})
                WHERE c.ts >= $from AND c.ts < $to
                OPTIONAL MATCH (c)<-[:AUTHORED]-(a:Author)
                RETURN c.sha AS sha, c.ts AS ts, c.summary AS summary,
                       a.name AS author, coalesce(m.added, 0) AS added,
                       coalesce(m.removed, 0) AS removed
                ORDER BY c.ts DESC, c.seq ASC
                LIMIT {MAX_HISTORY_COMMITS}
                """,
                {**params, "path": file},
            ).result_set
            total = (
                self._scalar(
                    "MATCH (c:Commit)-[:MODIFIED]->(f:File {path: $path}) "
                    "WHERE c.ts >= $from AND c.ts < $to RETURN count(c) AS n",
                    {**params, "path": file},
                )
                or 0
            )
            commits = [
                {
                    "sha": r[0][:10],
                    "ts": r[1],
                    "summary": r[2],
                    "author": r[3],
                    "added": int(r[4] or 0),
                    "removed": int(r[5] or 0),
                }
                for r in rows
            ]
            top_files: list[dict] = []
        else:
            rows = self._ro(
                f"""
                MATCH (c:Commit)
                WHERE c.ts >= $from AND c.ts < $to
                OPTIONAL MATCH (c)<-[:AUTHORED]-(a:Author)
                OPTIONAL MATCH (c)-[m:MODIFIED]->(f:File)
                WITH c, a, count(DISTINCT f) AS touches,
                     sum(coalesce(m.added, 0)) AS added,
                     sum(coalesce(m.removed, 0)) AS removed
                RETURN c.sha AS sha, c.ts AS ts, c.summary AS summary,
                       a.name AS author, touches, added, removed
                ORDER BY c.ts DESC, c.seq ASC
                LIMIT {MAX_HISTORY_COMMITS}
                """,
                params,
            ).result_set
            total = (
                self._scalar(
                    "MATCH (c:Commit) WHERE c.ts >= $from AND c.ts < $to "
                    "RETURN count(c) AS n",
                    params,
                )
                or 0
            )
            commits = [
                {
                    "sha": r[0][:10],
                    "ts": r[1],
                    "summary": r[2],
                    "author": r[3],
                    "touches": int(r[4] or 0),
                    "added": int(r[5] or 0),
                    "removed": int(r[6] or 0),
                }
                for r in rows
            ]
            file_rows = self._ro(
                f"""
                MATCH (c:Commit)-[m:MODIFIED]->(f:File)
                WHERE c.ts >= $from AND c.ts < $to
                RETURN f.path AS path, count(DISTINCT c) AS commits,
                       sum(coalesce(m.added, 0)) AS added,
                       sum(coalesce(m.removed, 0)) AS removed
                ORDER BY added + removed DESC, path ASC
                LIMIT {MAX_HISTORY_FILES}
                """,
                params,
            ).result_set
            top_files = [
                {
                    "path": r[0],
                    "commits": r[1],
                    "added": int(r[2] or 0),
                    "removed": int(r[3] or 0),
                }
                for r in file_rows
            ]
            distinct = (
                self._scalar(
                    "MATCH (c:Commit)-[:MODIFIED]->(f:File) "
                    "WHERE c.ts >= $from AND c.ts < $to "
                    "RETURN count(DISTINCT f) AS n",
                    params,
                )
                or 0
            )
            if distinct > len(top_files):
                notes.append(
                    f"top files capped at {MAX_HISTORY_FILES} "
                    f"({distinct - len(top_files)} not shown)"
                )
        if total > len(commits):
            notes.append(
                f"window commits capped at {MAX_HISTORY_COMMITS} "
                f"({total - len(commits)} not shown)"
            )
        if not commits:
            notes.append("no commits in this window")
        return {"from": frm, "to": to, "commits": commits, "top_files": top_files}

    def _graph_impact(self, query: dict) -> dict:
        """Blast radius (M4R): the inverted call cone above one symbol.

        "What breaks if this changes?" is a reverse CALLS walk — the same
        primitive as the ``callers`` tool, widened for the console: one row
        per affected symbol at its shortest distance (counted once, not once
        per path), each carrying the chain that reaches the target, plus a
        file rollup split into source and test files.

        Honesty rules: guessed (weak) edges are excluded by default and the
        peers reachable only through them are counted; every cap (walk,
        symbol limit, file display) is stated with exact counts; a symbol
        with no callers is a real answer — an entry point — not an empty
        error. Test detection is a path heuristic, and says so.
        """
        symbol = (query.get("symbol") or [""])[0].strip()
        if not symbol:
            raise ApiError(400, "pass ?symbol=<name or qualname>")
        file = (query.get("file") or [None])[0] or None
        depth_raw = (query.get("depth") or [None])[0]
        depth = 4
        if depth_raw is not None:
            try:
                depth = int(depth_raw)
            except ValueError:
                raise ApiError(400, "depth must be an integer")
            if not 1 <= depth <= MAX_IMPACT_DEPTH:
                raise ApiError(400, f"depth must be 1..{MAX_IMPACT_DEPTH}")
        limit_raw = (query.get("limit") or [None])[0]
        limit = 120
        if limit_raw is not None:
            try:
                limit = int(limit_raw)
            except ValueError:
                raise ApiError(400, "limit must be an integer")
            if not 1 <= limit <= MAX_IMPACT_SYMBOLS:
                raise ApiError(400, f"limit must be 1..{MAX_IMPACT_SYMBOLS}")
        include_weak = (query.get("include_weak") or ["0"])[0].lower() in (
            "1",
            "true",
            "yes",
        )

        scope = " AND s.file = $file" if file else ""
        params: dict = {"name": symbol, "limit": 25}
        if file:
            params["file"] = file
        candidates = self._ro(
            f"""
            MATCH (s:Symbol)
            WHERE (s.qualname = $name OR s.name = $name){scope}
            RETURN s.file AS file, s.qualname AS qualname, s.name AS name,
                   s.kind AS kind, s.lineno AS lineno,
                   s.qualname = $name AS qual_hit
            ORDER BY qual_hit DESC, file, qualname
            LIMIT $limit
            """,
            params,
        ).result_set
        pool = [row for row in candidates if row[5]] or list(candidates)
        where = f" in {file}" if file else ""
        if not pool:
            raise ApiError(404, f"no Symbol named `{symbol}`{where}")
        if len(pool) > 1:
            names = ", ".join(f"{row[0]}:{row[1]}" for row in pool[:8])
            raise ApiError(
                400,
                "ambiguous symbol — pass ?file= or the exact qualname; "
                f"candidates: {names}",
            )
        target = dict(
            zip(
                ("file", "qualname", "name", "kind", "lineno"),
                pool[0][:5],
                strict=True,
            )
        )

        conditions = ["(c.file <> $file OR c.qualname <> $qualname)"]
        if not include_weak:
            conditions.append(
                "all(r IN relationships(p) WHERE coalesce(r.weak, false) = false)"
            )
        cap = max(limit * 8, 400)
        rows = self._ro(
            f"""
            MATCH p=(c:Symbol)-[:CALLS*1..{depth}]->
                    (t:Symbol {{file: $file, qualname: $qualname}})
            WHERE {" AND ".join(conditions)}
            RETURN c.file AS file, c.qualname AS caller, c.name AS name,
                   c.kind AS kind, length(p) AS hops,
                   [n IN nodes(p) | {{file: n.file, qualname: n.qualname}}] AS chain,
                   [r IN relationships(p) | coalesce(r.count, 0)] AS counts,
                   [r IN relationships(p) | coalesce(r.weak, false)] AS weaks
            ORDER BY hops, file, caller
            LIMIT {cap}
            """,
            {"file": target["file"], "qualname": target["qualname"]},
        ).result_set

        deduped: dict[tuple[str, str], dict] = {}
        for row in rows:
            key = (row[0], row[1])
            entry = {
                "file": row[0],
                "qualname": row[1],
                "name": row[2],
                "kind": row[3],
                "hops": int(row[4]),
                "chain": row[5],
                "counts": row[6],
                "weak": any(bool(flag) for flag in row[7]),
            }
            kept = deduped.get(key)
            if kept is None:
                deduped[key] = entry
            elif kept["weak"] and not entry["weak"]:
                # Same caller, same shortest distance: keep the verified
                # chain over a guessed one rather than the first row seen.
                deduped[key] = entry

        ordered = sorted(
            deduped.values(),
            key=lambda row: (row["hops"], row["file"], row["qualname"]),
        )
        shown = ordered[:limit]
        truncated = len(ordered) > limit
        walk_capped = len(rows) >= cap

        files_map: dict[str, dict] = {}
        for row in shown:
            item = files_map.get(row["file"])
            if item is None:
                files_map[row["file"]] = {
                    "path": row["file"],
                    "symbols": 1,
                    "min_hops": row["hops"],
                    "tests": _is_test_path(row["file"]),
                }
            else:
                item["symbols"] += 1
                item["min_hops"] = min(item["min_hops"], row["hops"])
        files_total = len(files_map)
        test_files = sum(1 for item in files_map.values() if item["tests"])
        files = sorted(
            files_map.values(), key=lambda item: (item["min_hops"], item["path"])
        )

        notes = [f"target = {target['qualname']} ({target['file']})"]
        if not ordered:
            notes.append(
                f"no callers within {depth} hops — this symbol is an entry "
                "point in this graph (nothing resolved calls it)"
            )
        if walk_capped:
            notes.append(
                f"call-path walk hit its cap ({cap} paths) — counts below "
                "are floors, not a census"
            )
        if truncated:
            scope_note = "at least " if walk_capped else ""
            notes.append(
                f"showing {limit} of {scope_note}{len(ordered)} affected "
                f"symbols (limit={limit})"
            )
        if not include_weak and ordered:
            hidden_rows = self._ro(
                f"""
                MATCH p=(c:Symbol)-[:CALLS*1..{depth}]->
                        (t:Symbol {{file: $file, qualname: $qualname}})
                WHERE (c.file <> $file OR c.qualname <> $qualname)
                  AND any(r IN relationships(p) WHERE coalesce(r.weak, false))
                RETURN DISTINCT c.file AS file, c.qualname AS qualname
                LIMIT {cap}
                """,
                {"file": target["file"], "qualname": target["qualname"]},
            ).result_set
            hidden = {(row[0], row[1]) for row in hidden_rows} - {
                (row["file"], row["qualname"]) for row in shown
            }
            if hidden:
                floor = "at least " if len(hidden_rows) >= cap else ""
                notes.append(
                    f"{floor}{len(hidden)} caller(s) hidden by the weak-edge "
                    "filter — pass include_weak=true to include"
                )
        if files_total > MAX_IMPACT_FILES:
            notes.append(
                f"affected files capped at {MAX_IMPACT_FILES} "
                f"({files_total - MAX_IMPACT_FILES} not shown)"
            )
            files = files[:MAX_IMPACT_FILES]
        if shown:
            notes.append(
                "test files are a path heuristic (tests/, test_*.py, "
                "*_test.py), not runtime evidence"
            )
            if not test_files:
                notes.append(
                    "no test file appears in the blast radius — review test "
                    "coverage before changing this"
                )

        levels: dict[int, list] = {}
        for row in shown:
            levels.setdefault(row["hops"], []).append(
                {
                    "file": row["file"],
                    "qualname": row["qualname"],
                    "name": row["name"],
                    "kind": row["kind"],
                    "chain": row["chain"],
                    "counts": row["counts"],
                    "weak": row["weak"],
                }
            )
        return {
            "target": target,
            "params": {
                "depth": depth,
                "include_weak": include_weak,
                "limit": limit,
            },
            "summary": {
                "direct_callers": sum(1 for row in ordered if row["hops"] == 1),
                "transitive_callers": len(ordered),
                "affected_files": files_total,
                "affected_test_files": test_files,
                "max_hops": max((row["hops"] for row in ordered), default=0),
                "shown": len(shown),
                "truncated": truncated or files_total > MAX_IMPACT_FILES,
                "walk_capped": walk_capped,
            },
            "levels": [
                {"hops": hops, "symbols": levels[hops]} for hops in sorted(levels)
            ],
            "files": files,
            "notes": notes,
        }

    # -- ownership (M4R) ----------------------------------------------------

    def _graph_ownership(self, query: dict) -> dict:
        """Who knows what (M4R): ownership inferred from commit history.

        Two scopes, one contract. Repo scope (no params) ranks the people
        with the most diff-bearing history, shows the hottest files with
        their owner mix, and names single-owner files (bus-factor risk).
        File scope (``?file=`` or ``?symbol=``) ranks that file's authors
        with recency, lists its recent commits, and suggests reviewers.

        Honesty rules that shaped the queries: ownership is *inferred* —
        the graph holds no CODEOWNERS — and only diff-bearing commits carry
        file attribution (merges do not). Authors are grouped by email, so
        one human with several emails is several rows. History is keyed by
        the path at commit time; the file payload surfaces same-basename
        paths with disjoint windows as likely predecessors instead of
        silently merging them. Every cap is exact and reported.
        """
        file = (query.get("file") or [None])[0] or None
        symbol = (query.get("symbol") or [None])[0] or None
        if symbol:
            scope = " AND s.file = $file" if file else ""
            params: dict = {"name": symbol, "limit": 25}
            if file:
                params["file"] = file
            candidates = self._ro(
                f"""
                MATCH (s:Symbol)
                WHERE (s.qualname = $name OR s.name = $name){scope}
                RETURN s.file AS file, s.qualname AS qualname,
                       s.qualname = $name AS qual_hit
                ORDER BY qual_hit DESC, file, qualname
                LIMIT $limit
                """,
                params,
            ).result_set
            pool = [row for row in candidates if row[2]] or list(candidates)
            if not pool:
                where = f" in {file}" if file else ""
                raise ApiError(404, f"no Symbol named `{symbol}`{where}")
            if len(pool) > 1:
                names = ", ".join(f"{row[0]}:{row[1]}" for row in pool[:8])
                raise ApiError(
                    400,
                    "ambiguous symbol — pass ?file= or the exact qualname; "
                    f"candidates: {names}",
                )
            file = pool[0][0]
        if file:
            return self._ownership_file(file)
        return self._ownership_repo()

    def _ownership_repo(self) -> dict:
        """Repo-wide ownership: people, hot files, and single-owner files."""
        clock = self._scalar("MATCH (c:Commit) RETURN max(c.ts)")
        if clock is None:
            return {
                "scope": "repo",
                "clock": None,
                "recent_days": OWNERSHIP_RECENT_DAYS,
                "totals": {
                    "commits": 0,
                    "commits_with_diff": 0,
                    "files": 0,
                    "authors": 0,
                    "file_changes": 0,
                },
                "people": [],
                "people_total": 0,
                "hot_files": [],
                "hot_files_total": 0,
                "risks": [],
                "risks_total": 0,
                "notes": ["no commits in this graph — ownership is unknown"],
            }
        cutoff = int(clock) - OWNERSHIP_RECENT_DAYS * 86400
        commits = self._scalar("MATCH (c:Commit) RETURN count(c)") or 0
        commits_with_diff = (
            self._scalar(
                "MATCH (c:Commit)-[:MODIFIED]->(:File) RETURN count(DISTINCT c)"
            )
            or 0
        )
        files_total = self._scalar("MATCH (f:File) RETURN count(f)") or 0
        file_changes = (
            self._scalar("MATCH (:Commit)-[m:MODIFIED]->(:File) RETURN count(m)") or 0
        )
        authors_total = (
            self._scalar(
                "MATCH (a:Author)-[:AUTHORED]->(:Commit)-[:MODIFIED]->(:File) "
                "RETURN count(DISTINCT a)"
            )
            or 0
        )

        people_rows = self._ro(
            """
            MATCH (a:Author)-[:AUTHORED]->(c:Commit)-[m:MODIFIED]->(f:File)
            RETURN a.email AS email, a.name AS name,
                   count(DISTINCT c) AS commits, count(DISTINCT f) AS files,
                   sum(coalesce(m.added, 0) + coalesce(m.removed, 0)) AS churn,
                   max(c.ts) AS last_ts
            ORDER BY commits DESC, last_ts DESC, email
            LIMIT $limit
            """,
            {"limit": MAX_OWNERS},
        ).result_set
        recent_rows = self._ro(
            """
            MATCH (a:Author)-[:AUTHORED]->(c:Commit)-[:MODIFIED]->(:File)
            WHERE c.ts >= $cutoff
            RETURN a.email AS email, count(DISTINCT c) AS commits
            """,
            {"cutoff": cutoff},
        ).result_set
        recent = {row[0]: int(row[1]) for row in recent_rows}
        people = [
            {
                "name": row[1],
                "email": row[0],
                "commits": int(row[2]),
                "files": int(row[3]),
                "churn": int(row[4] or 0),
                "last_ts": int(row[5]) if row[5] else None,
                "recent_commits": recent.get(row[0], 0),
                "share": (
                    round(row[2] / commits_with_diff, 3) if commits_with_diff else 0.0
                ),
                "bot": _is_bot_name(row[1]),
            }
            for row in people_rows
        ]

        file_rows = self._ro("""
            MATCH (a:Author)-[:AUTHORED]->(c:Commit)-[m:MODIFIED]->(f:File)
            RETURN f.path AS path, a.email AS email, a.name AS name,
                   count(DISTINCT c) AS commits,
                   sum(coalesce(m.added, 0) + coalesce(m.removed, 0)) AS churn,
                   max(c.ts) AS last_ts
            ORDER BY path, commits DESC
            """).result_set
        per_file: dict[str, dict] = {}
        for path, email, name, fcommits, churn, last_ts in file_rows:
            entry = per_file.setdefault(
                path,
                {
                    "path": path,
                    "commits": 0,
                    "churn": 0,
                    "last_ts": 0,
                    "owners": [],
                },
            )
            entry["commits"] += int(fcommits)
            entry["churn"] += int(churn or 0)
            entry["last_ts"] = max(entry["last_ts"], int(last_ts or 0))
            entry["owners"].append(
                {
                    "name": name,
                    "email": email,
                    "commits": int(fcommits),
                    "churn": int(churn or 0),
                    "last_ts": int(last_ts) if last_ts else None,
                    "bot": _is_bot_name(name),
                }
            )
        for entry in per_file.values():
            entry["owners"].sort(key=lambda owner: (-owner["commits"], owner["email"]))

        hot_sorted = sorted(
            per_file.values(),
            key=lambda entry: (-entry["commits"], -entry["churn"], entry["path"]),
        )
        hot_files = []
        owners_trimmed = 0
        for entry in hot_sorted[:MAX_OWNERSHIP_FILES]:
            owners = entry["owners"]
            if len(owners) > MAX_HOT_FILE_OWNERS:
                owners_trimmed += 1
            hot_files.append(
                {
                    "path": entry["path"],
                    "commits": entry["commits"],
                    "churn": entry["churn"],
                    "authors": len(owners),
                    "last_ts": entry["last_ts"] or None,
                    "tests": _is_test_path(entry["path"]),
                    "owners": [
                        {
                            "name": owner["name"],
                            "email": owner["email"],
                            "commits": owner["commits"],
                            "share": round(owner["commits"] / entry["commits"], 3),
                            "bot": owner["bot"],
                        }
                        for owner in owners[:MAX_HOT_FILE_OWNERS]
                    ],
                }
            )

        risk_rows = []
        for entry in per_file.values():
            if entry["commits"] < OWNERSHIP_RISK_MIN_COMMITS:
                continue
            top = entry["owners"][0]
            share = top["commits"] / entry["commits"]
            if share < OWNERSHIP_RISK_SHARE:
                continue
            risk_rows.append(
                {
                    "path": entry["path"],
                    "commits": entry["commits"],
                    "churn": entry["churn"],
                    "authors": len(entry["owners"]),
                    "primary_share": round(share, 3),
                    "last_ts": entry["last_ts"] or None,
                    "tests": _is_test_path(entry["path"]),
                    "owners": [
                        {
                            "name": owner["name"],
                            "email": owner["email"],
                            "commits": owner["commits"],
                            "share": round(owner["commits"] / entry["commits"], 3),
                            "bot": owner["bot"],
                        }
                        for owner in entry["owners"][:MAX_HOT_FILE_OWNERS]
                    ],
                }
            )
        risk_rows.sort(key=lambda row: (-row["commits"], row["path"]))
        risks_total = len(risk_rows)

        notes = [
            (
                "ownership is inferred from diff-bearing commit history — the "
                "graph holds no CODEOWNERS"
            ),
            (
                f"{commits_with_diff} of {commits} commits carry file attribution "
                "(merge/no-diff commits do not)"
            ),
            (
                "authors are grouped by email — one person with several emails "
                "appears more than once"
            ),
            (
                "bot detection is a name heuristic ('[bot]' suffix); bots are "
                "counted, not hidden"
            ),
            (
                f"single-owner files = at least {OWNERSHIP_RISK_MIN_COMMITS} "
                f"commits with at least {int(OWNERSHIP_RISK_SHARE * 100)}% by "
                "one author"
            ),
            (
                "history is keyed by path at commit time — renames split a "
                "file's ownership"
            ),
        ]
        if authors_total > len(people):
            notes.append(
                f"people capped at {MAX_OWNERS} ({authors_total - len(people)} "
                "more not shown; every row above is exact)"
            )
        if len(hot_sorted) > MAX_OWNERSHIP_FILES:
            notes.append(
                f"hot files capped at {MAX_OWNERSHIP_FILES} "
                f"({len(hot_sorted) - MAX_OWNERSHIP_FILES} not shown)"
            )
        if owners_trimmed:
            notes.append(
                f"per-file owner lists capped at {MAX_HOT_FILE_OWNERS} "
                f"({owners_trimmed} hot file(s) had more)"
            )
        if risks_total > MAX_OWNERSHIP_RISKS:
            notes.append(
                f"single-owner files capped at {MAX_OWNERSHIP_RISKS} "
                f"({risks_total - MAX_OWNERSHIP_RISKS} not shown)"
            )
        return {
            "scope": "repo",
            "clock": int(clock),
            "recent_days": OWNERSHIP_RECENT_DAYS,
            "totals": {
                "commits": int(commits),
                "commits_with_diff": int(commits_with_diff),
                "files": int(files_total),
                "authors": int(authors_total),
                "file_changes": int(file_changes),
            },
            "people": people,
            "people_total": int(authors_total),
            "hot_files": hot_files,
            "hot_files_total": len(hot_sorted),
            "risks": risk_rows[:MAX_OWNERSHIP_RISKS],
            "risks_total": risks_total,
            "notes": notes,
        }

    def _ownership_file(self, path: str) -> dict:
        """One file's owners, recent commits, and likely-renamed predecessors."""
        meta = self._ro(
            "MATCH (f:File {path: $path}) RETURN f.lang AS lang, f.loc AS loc",
            {"path": path},
        ).result_set
        if not meta:
            raise ApiError(404, f"no File `{path}` in {self.graph}")
        clock = self._scalar("MATCH (c:Commit) RETURN max(c.ts)")
        cutoff = (int(clock) if clock else 0) - OWNERSHIP_RECENT_DAYS * 86400

        total_row = self._ro(
            """
            MATCH (c:Commit)-[m:MODIFIED]->(f:File {path: $path})
            RETURN count(c) AS commits,
                   sum(coalesce(m.added, 0)) AS added,
                   sum(coalesce(m.removed, 0)) AS removed,
                   min(c.ts) AS first_ts, max(c.ts) AS last_ts
            """,
            {"path": path},
        ).result_set
        total_commits, added, removed, first_ts, last_ts = (
            total_row[0] if total_row else (0, 0, 0, None, None)
        )
        added = int(added or 0)
        removed = int(removed or 0)

        owner_rows = self._ro(
            """
            MATCH (a:Author)-[:AUTHORED]->(c:Commit)-[m:MODIFIED]->(f:File {path: $path})
            RETURN a.email AS email, a.name AS name,
                   count(DISTINCT c) AS commits,
                   sum(coalesce(m.added, 0)) AS added,
                   sum(coalesce(m.removed, 0)) AS removed,
                   min(c.ts) AS first_ts, max(c.ts) AS last_ts
            ORDER BY commits DESC, last_ts DESC, email
            """,
            {"path": path},
        ).result_set
        recent_owner_rows = self._ro(
            """
            MATCH (a:Author)-[:AUTHORED]->(c:Commit)-[:MODIFIED]->(f:File {path: $path})
            WHERE c.ts >= $cutoff
            RETURN a.email AS email, count(DISTINCT c) AS commits
            """,
            {"path": path, "cutoff": cutoff},
        ).result_set
        recent_by_owner = {row[0]: int(row[1]) for row in recent_owner_rows}
        owners = [
            {
                "name": row[1],
                "email": row[0],
                "commits": int(row[2]),
                "added": int(row[3] or 0),
                "removed": int(row[4] or 0),
                "churn": int(row[3] or 0) + int(row[4] or 0),
                "first_ts": int(row[5]) if row[5] else None,
                "last_ts": int(row[6]) if row[6] else None,
                "recent_commits": recent_by_owner.get(row[0], 0),
                "share": round(row[2] / total_commits, 3) if total_commits else 0.0,
                "bot": _is_bot_name(row[1]),
            }
            for row in owner_rows
        ]

        reviewers = []
        if owners:
            ranked = sorted(
                owners,
                key=lambda owner: (
                    -(owner["recent_commits"] * 2 + owner["commits"]),
                    -owner["commits"],
                    owner["email"],
                ),
            )[:MAX_SUGGESTED_REVIEWERS]
            reviewers = [
                {
                    "name": owner["name"],
                    "email": owner["email"],
                    "commits": owner["commits"],
                    "recent_commits": owner["recent_commits"],
                    "last_ts": owner["last_ts"],
                    "why": (
                        f"{owner['commits']} commit(s) "
                        f"({owner['recent_commits']} in the last "
                        f"{OWNERSHIP_RECENT_DAYS} days), last touch "
                        f"{_utc_date(owner['last_ts'])}"
                    ),
                }
                for owner in ranked
            ]

        recent_rows = self._ro(
            """
            MATCH (a:Author)-[:AUTHORED]->(c:Commit)-[m:MODIFIED]->(f:File {path: $path})
            RETURN c.sha AS sha, c.summary AS summary, c.ts AS ts,
                   a.name AS name, a.email AS email,
                   coalesce(m.added, 0) AS added,
                   coalesce(m.removed, 0) AS removed
            ORDER BY c.ts DESC, c.seq DESC
            LIMIT $limit
            """,
            {"path": path, "limit": MAX_OWNERSHIP_COMMITS},
        ).result_set
        recent_total = (
            self._scalar(
                "MATCH (c:Commit)-[:MODIFIED]->(f:File {path: $path}) "
                "WHERE c.ts >= $cutoff RETURN count(c)",
                {"path": path, "cutoff": cutoff},
            )
            or 0
        )

        related: list[dict] = []
        if first_ts and last_ts:
            # The src-layout move is the one rename this graph can name
            # without guessing: same path with/without the ``src/`` prefix.
            # Basename matching was tried and rejected — it paired unrelated
            # files that happen to share a name (tests/helpers.py vs
            # flask/helpers.py), which is exactly the slop this view avoids.
            alias = path[4:] if path.startswith("src/") else f"src/{path}"
            alias_rows = self._ro(
                """
                MATCH (c:Commit)-[:MODIFIED]->(f:File {path: $alias})
                RETURN count(c) AS commits, min(c.ts) AS first_ts,
                       max(c.ts) AS last_ts
                """,
                {"alias": alias},
            ).result_set
            if alias_rows and alias_rows[0][0]:
                a_commits, a_first, a_last = alias_rows[0]
                related.append(
                    {
                        "path": alias,
                        "commits": int(a_commits),
                        "first_ts": int(a_first) if a_first else None,
                        "last_ts": int(a_last) if a_last else None,
                        "relation": (
                            "earlier"
                            if a_first and int(a_first) < int(first_ts)
                            else "later"
                        ),
                    }
                )

        notes = [
            (
                "ownership is inferred from diff-bearing commit history — the "
                "graph holds no CODEOWNERS"
            ),
            (
                "a commit appears here only if it carries a diff for this path "
                "(merge commits never do)"
            ),
            (
                "authors are grouped by email — one person with several emails "
                "appears more than once"
            ),
            (
                "bot detection is a name heuristic ('[bot]' suffix); bots are "
                "counted, not hidden"
            ),
        ]
        if not owners:
            notes.append(
                "no diff-bearing commits touch this path — ownership is "
                "unknown (new file, or only renamed into place)"
            )
        if owners and recent_total == 0:
            notes.append(
                f"no commits in the last {OWNERSHIP_RECENT_DAYS} days — "
                "these owners may be stale"
            )
        if len(owners) > MAX_OWNERS:
            notes.append(
                f"owners capped at {MAX_OWNERS} ({len(owners) - MAX_OWNERS} "
                "more not shown; shares are over all commits)"
            )
        if reviewers:
            notes.append(
                "suggested reviewers rank owners by 2x recent + all-time "
                "commits — a heuristic, not policy"
            )
        if related:
            notes.append(
                "related path = this path with/without the src/ prefix — a "
                "likely pre-src-layout path; its history is not merged into "
                "these totals"
            )
        if _is_test_path(path):
            notes.append(
                "test paths are a heuristic (tests/, test_*.py, *_test.py), "
                "not runtime evidence"
            )
        return {
            "scope": "file",
            "path": path,
            "clock": int(clock) if clock else None,
            "recent_days": OWNERSHIP_RECENT_DAYS,
            "file": {
                "path": path,
                "lang": meta[0][0],
                "loc": meta[0][1],
                "commits": int(total_commits),
                "authors": len(owners),
                "churn": added + removed,
                "added": added,
                "removed": removed,
                "first_ts": int(first_ts) if first_ts else None,
                "last_ts": int(last_ts) if last_ts else None,
                "symbols": int(
                    self._scalar(
                        "MATCH (:File {path: $path})-[:DEFINES]->(s:Symbol) "
                        "RETURN count(s)",
                        {"path": path},
                    )
                    or 0
                ),
                "tests": _is_test_path(path),
            },
            "owners": owners[:MAX_OWNERS],
            "owners_total": len(owners),
            "reviewers": reviewers,
            "recent_commits": [
                {
                    "sha": row[0],
                    "summary": row[1],
                    "ts": int(row[2]) if row[2] else None,
                    "author": row[3],
                    "email": row[4],
                    "added": int(row[5]),
                    "removed": int(row[6]),
                }
                for row in recent_rows
            ],
            "recent_total": int(recent_total),
            "related_paths": related,
            "notes": notes,
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

    def _graph_induced(self, body: dict) -> dict:
        """Induced subgraph of an Ask step's evidence (M4R "Trace Ribbon").

        The client sends plain refs per step — the same ``(file, qualname)`` /
        path / module / commit-sha values its tool result points at. We resolve
        them to graph nodes and add the typed edges *among* those nodes.
        Nothing is guessed: unresolved refs and every cap land in ``notes``,
        and edges keep their ``weak`` flag for the UI to label.
        """
        steps = body.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ApiError(
                400,
                'pass {"steps": [{"id": "...", "symbols": [{"file", "qualname"}],'
                ' "files": [...], "modules": [...], "commits": [{"sha"}]}]}',
            )
        if len(steps) > MAX_INDUCED_STEPS:
            raise ApiError(400, f"too many steps ({MAX_INDUCED_STEPS} max)")

        out: list[dict] = []
        for item in steps:
            if not isinstance(item, dict):
                raise ApiError(400, "each step must be an object")
            sid = item.get("id")
            if not isinstance(sid, str) or not sid.strip():
                raise ApiError(400, "each step needs a non-empty string id")
            notes: list[str] = []
            pairs = _induced_refs(
                item.get("symbols"),
                ("file", "qualname"),
                MAX_STEP_SYMBOLS,
                "symbol",
                notes,
            )
            paths = _induced_refs(
                item.get("files"), ("path",), MAX_STEP_FILES, "file", notes
            )
            names = _induced_refs(
                item.get("modules"), ("name",), MAX_STEP_MODULES, "module", notes
            )
            shas = _induced_refs(
                item.get("commits"), ("sha",), MAX_STEP_COMMITS, "commit", notes
            )
            nodes, edges = self._induced_elements(pairs, paths, names, shas, notes)
            payload = finalize(nodes, edges, notes=notes)
            payload["id"] = sid
            out.append(payload)
        return {"steps": out}

    def _induced_elements(
        self,
        pairs: list[dict],
        paths: list[dict],
        names: list[dict],
        shas: list[dict],
        notes: list[str],
    ) -> tuple[dict[int, dict], dict[int, dict]]:
        """Resolve refs to nodes; absorb typed edges among them."""
        nodes: dict[int, dict] = {}
        edges: dict[int, dict] = {}
        expected = 0

        def absorb(result) -> None:
            columns = [header[1] for header in result.header]
            found, found_edges, _ = collect(result.result_set, columns)
            nodes.update(found)
            edges.update(found_edges)

        if pairs:
            expected += len(pairs)
            absorb(
                self._ro(
                    "UNWIND $pairs AS pr "
                    "MATCH (s:Symbol {file: pr.file, qualname: pr.qualname}) RETURN s",
                    {"pairs": pairs},
                )
            )
        if paths:
            expected += len(paths)
            absorb(
                self._ro(
                    "UNWIND $paths AS p MATCH (f:File {path: p}) RETURN f",
                    {"paths": [p["path"] for p in paths]},
                )
            )
        if names:
            expected += len(names)
            absorb(
                self._ro(
                    "UNWIND $names AS n MATCH (m:Module {name: n}) RETURN m",
                    {"names": [n["name"] for n in names]},
                )
            )
        if shas:
            expected += len(shas)
            absorb(
                self._ro(
                    # STARTS WITH tolerates abbreviated shas; the client
                    # sends the full values from the recorded rows.
                    "UNWIND $shas AS s MATCH (c:Commit) "
                    "WHERE c.sha STARTS WITH s.sha RETURN c",
                    {"shas": shas},
                )
            )

        symbol_ids = [nid for nid, node in nodes.items() if node["type"] == "Symbol"]
        file_ids = [nid for nid, node in nodes.items() if node["type"] == "File"]
        module_ids = [nid for nid, node in nodes.items() if node["type"] == "Module"]
        commit_ids = [nid for nid, node in nodes.items() if node["type"] == "Commit"]
        if len(symbol_ids) >= 2:
            absorb(
                self._ro(
                    "MATCH (a:Symbol)-[r:CALLS]->(b:Symbol) "
                    "WHERE id(a) IN $ids AND id(b) IN $ids RETURN a, r, b",
                    {"ids": symbol_ids},
                )
            )
        if file_ids and module_ids:
            absorb(
                self._ro(
                    "MATCH (f:File)-[r:IMPORTS]->(m:Module) "
                    "WHERE id(f) IN $fids AND id(m) IN $mids RETURN f, r, m",
                    {"fids": file_ids, "mids": module_ids},
                )
            )
        if file_ids and symbol_ids:
            absorb(
                self._ro(
                    "MATCH (f:File)-[r:DEFINES]->(s:Symbol) "
                    "WHERE id(f) IN $fids AND id(s) IN $sids RETURN f, r, s",
                    {"fids": file_ids, "sids": symbol_ids},
                )
            )
        if commit_ids and file_ids:
            # History steps replay as a commit → file chain (Gate A W1).
            absorb(
                self._ro(
                    "MATCH (c:Commit)-[r:MODIFIED]->(f:File) "
                    "WHERE id(c) IN $cids AND id(f) IN $fids RETURN c, r, f",
                    {"cids": commit_ids, "fids": file_ids},
                )
            )
        if len(commit_ids) >= 2:
            absorb(
                self._ro(
                    "MATCH (a:Commit)-[r:PARENT]->(b:Commit) "
                    "WHERE id(a) IN $ids AND id(b) IN $ids RETURN a, r, b",
                    {"ids": commit_ids},
                )
            )

        if expected and len(nodes) < expected:
            notes.append(
                f"{expected - len(nodes)} evidence ref(s) did not resolve in this graph"
            )
        weak = sum(1 for edge in edges.values() if edge["props"].get("weak") is True)
        if weak:
            notes.append(f"{weak} weak edge(s) shown — resolver guesses (dashed)")
        return nodes, edges

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

    # -- ask + ask/stream --------------------------------------------------

    def _ask_prelude(self, body: dict) -> _AskSetup:
        """Validate one ask and resolve everything request-scoped up front.

        Shared by the blocking endpoint and the SSE stream, so both refuse
        identically (400/503/429 as plain JSON) before any stream starts.
        """
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
        warnings: list[str] = []
        # The demo graph is shared by every visitor. A signed-in user who has
        # no repo of their own still reads it, but their questions must not
        # write memories into everybody else's history — refuse honestly
        # instead of silently dropping the write.
        if (
            remember
            and self._request_user is not None
            and self.graph == self._demo_graph
        ):
            remember = False
            warnings.append(
                "memory writes need a repository of your own — the demo graph "
                "is shared, so this answer was not remembered"
            )
        ctx = self._request_ctx
        overrides = self._llm_overrides(ctx)
        llm = self._new_llm(overrides)
        if not llm.is_configured():
            if overrides:
                raise ApiError(
                    400,
                    "incomplete LLM settings — a request key needs a base URL "
                    "and model too (or clear the fields to use the server's)",
                    code="llm_settings",
                )
            raise ApiError(503, LLM_HINT)
        self._demo_quota_gate(ctx, overrides)
        return _AskSetup(
            question=question,
            max_steps=max_steps,
            remember=remember,
            warnings=warnings,
            overrides=overrides,
            llm=llm,
            graph=self.graph,
        )

    def _slack_events(self, ctx: RequestContext) -> tuple[int, str, bytes]:
        """Additive route: verify + enqueue one Slack Events API request."""
        from .slack.events import handle_request

        headers = dict(ctx.headers) if ctx.headers else {}
        status, payload = handle_request(headers, ctx.raw_body)
        return self._json(status, payload)

    def _github_webhook(self, ctx: RequestContext) -> tuple[int, str, bytes]:
        """Additive route: verify + enqueue one GitHub delivery, ack fast.

        The adapter lives in ``archivist.gh``; this method only hands over
        the raw body (HMAC) and headers. GitHub times out at 10 s, so the
        agent itself runs on the adapter's worker pool.
        """
        from .gh.webhook import handle_webhook

        headers = dict(ctx.headers) if ctx.headers else {}
        status, payload = handle_webhook(headers, ctx.raw_body)
        return self._json(status, payload)

    def _run_ask(
        self,
        setup: _AskSetup,
        *,
        on_event: Callable[[dict], None] | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> dict:
        """Execute one ask: episode bookkeeping, agent loop, finalize.

        Works on captured plain values only, so it can run on the stream's
        producer thread as well as on the request thread — the graph name
        comes from ``setup``, never from thread-locals.
        """
        repo = RepoGraph(graph=setup.graph, host=self.host, port=self.port)
        memory = (
            Memory(graph=setup.graph, host=self.host, port=self.port)
            if setup.remember
            else None
        )
        actions = Actions(graph=self.graph, host=self.host, port=self.port)
        try:
            episode_id = None
            if memory is not None:
                try:
                    rows = repo.query("MATCH (r:Repo) RETURN r.head AS head")[
                        "results"
                    ] or [{}]
                    episode_id = memory.record_episode(
                        kind="agent-session",
                        source="webapp",
                        question=setup.question,
                        graph=setup.graph,
                        model=setup.llm.model,
                        head=rows[0].get("head"),
                    )
                except Exception as exc:  # noqa: BLE001 - memory writes are best-effort
                    setup.warnings.append(f"memory write failed: {exc}")
            answer = Agent(
                repo,
                setup.llm,
                max_steps=setup.max_steps,
                memory=memory,
                episode_id=episode_id,
                actions=actions,
            ).ask(setup.question, on_event=on_event, cancel=cancel)
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
                    setup.warnings.append(f"memory write failed: {exc}")
                    episode_id = None
            return {
                "configured": True,
                "answer": answer.to_dict(),
                "episode_id": episode_id,
                "warnings": setup.warnings,
            }
        finally:
            repo.close()
            actions.close()
            if memory is not None:
                memory.close()

    def _ask(self, body: dict) -> dict:
        return self._run_ask(self._ask_prelude(body))

    def _ask_stream(self, body: dict) -> StreamResponse:
        """POST /api/ask/stream: the same run, as Server-Sent Events.

        Validation and quota happen now, on the request thread — refusals
        stay ordinary JSON errors (the console falls back to /api/ask and
        shows the message). The producer thread then streams ``step_start``
        / ``step_result`` / ``answer_delta`` frames and a terminal ``done``
        carrying the full Answer (trace included). A failure that prevents
        an Answer at all is a terminal ``error``.
        """
        setup = self._ask_prelude(body)

        def produce(abort: threading.Event, out: queue.Queue) -> None:
            def emit(event: dict) -> None:
                topic = str(event.get("type") or "message")
                payload = {k: v for k, v in event.items() if k != "type"}
                out.put(_sse_frame(topic, payload))

            try:
                result = self._run_ask(setup, on_event=emit, cancel=abort.is_set)
            except Exception as exc:  # noqa: BLE001 — terminal, honest
                out.put(
                    _sse_frame(
                        "error", {"message": f"{type(exc).__name__}: {exc}"[:500]}
                    )
                )
                return
            out.put(_sse_frame("done", result))

        return StreamResponse(produce)

    # -- BYO keys + demo quota (M7) ----------------------------------------

    def _llm_overrides(self, ctx: RequestContext) -> dict:
        """Per-request LLM settings from the ``X-Archivist-LLM-*`` headers.

        The browser keeps the visitor's key in localStorage and sends it on
        each ask; the server never stores, logs or echoes it. The base URL
        must be https — http is allowed only for loopback, so local
        vLLM/llama.cpp setups still work. Anything malformed is refused
        with a clean 400 rather than silently ignored.
        """
        headers = getattr(ctx, "headers", None) or {}
        base = _header_get(headers, LLM_HEADER_BASE).strip()
        model = _header_get(headers, LLM_HEADER_MODEL).strip()
        key = _header_get(headers, LLM_HEADER_KEY).strip()
        out: dict = {}
        if base:
            if len(base) > MAX_LLM_BASE_LEN:
                raise ApiError(
                    400,
                    f"LLM base URL too long (max {MAX_LLM_BASE_LEN} chars)",
                    code="llm_settings",
                )
            parsed = urlsplit(base)
            host = parsed.hostname or ""
            if parsed.scheme not in ("http", "https") or not host:
                raise ApiError(
                    400,
                    "LLM base URL must be an http(s) URL",
                    code="llm_settings",
                )
            if parsed.scheme == "http" and not _is_loopback_host(host):
                raise ApiError(
                    400,
                    "LLM base URL must use https (http is allowed only for "
                    "loopback addresses)",
                    code="llm_settings",
                )
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ApiError(
                    400,
                    "LLM base URL must not include credentials, a query or a "
                    "fragment",
                    code="llm_settings",
                )
            out["base_url"] = base.rstrip("/")
        if model:
            if len(model) > MAX_LLM_MODEL_LEN:
                raise ApiError(
                    400,
                    f"LLM model too long (max {MAX_LLM_MODEL_LEN} chars)",
                    code="llm_settings",
                )
            out["model"] = model
        if key:
            if len(key) > MAX_LLM_KEY_LEN:
                raise ApiError(
                    400,
                    f"LLM key too long (max {MAX_LLM_KEY_LEN} chars)",
                    code="llm_settings",
                )
            out["api_key"] = key
        return out

    def _new_llm(self, overrides: dict | None = None):
        """Request-scoped LLM client: BYO overrides > scripted seam > env.

        A custom factory (tests, capture server) always wins — scripts are
        deterministic by design, and BYO headers are for the real client.
        Fields not overridden fall back to the environment, so a visitor
        may point a key at any of their own endpoints field by field.
        """
        overrides = dict(overrides or {})
        if self._llm_factory is not ChatLLM:
            return self._llm_factory()
        return ChatLLM(
            base_url=overrides.get("base_url"),
            api_key=overrides.get("api_key"),
            model=overrides.get("model"),
        )

    def _demo_quota_gate(self, ctx: RequestContext, overrides: dict) -> None:
        """Cap asks that spend the server's demo key, per client, hourly.

        Only the shared key needs protecting: a visitor who brought their
        own key pays their own bill and is never counted. Scripted servers
        (tests, captures) are exempt — they spend nothing.
        """
        if overrides.get("api_key") or self.demo_quota <= 0:
            return
        if self._llm_factory is not ChatLLM:
            return
        if self._ask_limiter.hit(
            f"ask:{self._client_key(ctx)}", self.demo_quota, DEMO_QUOTA_WINDOW
        ):
            return
        raise ApiError(
            429,
            f"demo key quota reached ({self.demo_quota} questions/hour from "
            "this address) — add your own API key in Settings to keep asking",
            code="demo_quota",
        )

    def _llm_test(self, ctx: RequestContext) -> dict:
        """One tiny completion with the request's settings (rate-limited).

        Powers the console's "Test key" button: the request carries exactly
        the settings an ask would use, so a success here means asks will
        work too. Upstream failures come back as ``ok: false`` with the
        provider's words (never the key — it travels one way only).
        """
        overrides = self._llm_overrides(ctx)
        if not self._llm_test_limiter.hit(
            f"llmtest:{self._client_key(ctx)}", LLM_TEST_LIMIT, LLM_TEST_WINDOW
        ):
            raise ApiError(429, "too many key tests — try again in a few minutes")
        llm = self._new_llm(overrides)
        if not llm.is_configured():
            return {
                "ok": False,
                "error": "nothing to test yet — a base URL and model are "
                "required (plus a key unless the endpoint is keyless)",
            }
        started = time.perf_counter()
        try:
            response = llm.chat(
                [{"role": "user", "content": "Reply with the single word: pong"}]
            )
        except LLMError as exc:
            return {"ok": False, "error": str(exc)[:300]}
        return {
            "ok": True,
            "model": llm.model,
            "ms": round((time.perf_counter() - started) * 1000),
            "reply": (response.content or "").strip()[:200],
        }

    # -- helpers -----------------------------------------------------------

    def status_line(self) -> str:
        llm = self._new_llm({})
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
            app = self.server.app  # type: ignore[attr-defined]
            app.reset_headers()
            session = None
            token = None
            ctx = RequestContext()
            try:
                split = urlsplit(self.path)
                if method in ("GET", "HEAD") and split.path == "/":
                    # The root leads to Console v2 — the product's face now.
                    # (v1 stays archived at /classic/; its assets at /static/.)
                    payload = (
                        b'<!doctype html><meta http-equiv="refresh" '
                        b'content="0; url=/console/">'
                    )
                    self.send_response(302)
                    self.send_header("Location", "/console/")
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    if method != "HEAD":
                        self.wfile.write(payload)
                    return
                body: dict = {}
                raw = b""
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
                token = _cookie_value(self.headers.get("Cookie"), SESSION_COOKIE)
                accounts = app.accounts
                session = accounts.session_info(token) if accounts else None
                ctx = RequestContext(
                    user=session.user if session else None,
                    token=token,
                    headers=self.headers,
                    client_ip=str(self.client_address[0]),
                    secure=(self.headers.get("X-Forwarded-Proto") or "").lower()
                    == "https",
                    raw_body=raw,
                )
                result: tuple[int, str, bytes] | StreamResponse = app.dispatch(
                    method, split.path, parse_qs(split.query), body, ctx=ctx
                )
            except ApiError as exc:
                result = WebApp._json(
                    exc.status,
                    {
                        "error": exc.code or _ERROR_CODES.get(exc.status, "error"),
                        "message": exc.message,
                    },
                )
            except (ValueError, json.JSONDecodeError) as exc:
                result = WebApp._json(
                    400, {"error": "bad_request", "message": f"bad request: {exc}"}
                )
            extra_headers = app.take_headers()
            has_cookie = any(
                name.lower() == "set-cookie" for name, _value in extra_headers
            )
            if session is not None and session.refreshed and not has_cookie and token:
                # Sliding TTL: refresh the browser cookie when the server
                # slid the session (throttled by the store, not per request).
                extra_headers.append(
                    (
                        "Set-Cookie",
                        session_cookie(
                            token,
                            secure=ctx.secure,
                            max_age=max(0, session.expires_at - int(time.time())),
                        ),
                    )
                )
            if isinstance(result, StreamResponse):
                self._serve_stream(result, extra_headers)
                return
            status, content_type, payload = result
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            for name, value in extra_headers:
                self.send_header(name, value)
            self.end_headers()
            if method != "HEAD":
                self.wfile.write(payload)
            if close_after:
                self.close_connection = True

        def _serve_stream(
            self, resp: StreamResponse, extra_headers: list[tuple[str, str]]
        ) -> None:
            """Write an SSE response chunk by chunk, heartbeating while quiet.

            A failed write means the client went away: set ``resp.abort`` and
            stop writing. The producer checks the flag between steps and
            winds down on its own (its bookkeeping still runs); nothing here
            waits on the in-flight upstream call.
            """
            chunked = self.request_version == "HTTP/1.1"
            self.close_connection = True
            self.send_response(resp.status)
            self.send_header("Content-Type", resp.content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            if chunked:
                self.send_header("Transfer-Encoding", "chunked")
            for name, value in extra_headers:
                self.send_header(name, value)
            self.end_headers()
            resp.start()  # after the headers: a client gone this early costs no LLM call

            def write(data: bytes) -> None:
                if chunked:
                    self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
                else:
                    self.wfile.write(data)
                self.wfile.flush()

            try:
                while True:
                    try:
                        item = resp.queue.get(timeout=resp.heartbeat)
                    except queue.Empty:
                        item = b": ping\n\n"
                    if item is _FINAL:
                        break
                    write(item)
                if chunked:
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
            except OSError:
                resp.abort.set()  # client disconnected: cooperative stop

        def log_message(self, format: str, *args) -> None:
            if os.environ.get("ARCHIVIST_WEB_LOG"):
                print(f"[web] {self.address_string()} {format % args}")

    server = _BoundedThreadingHTTPServer(
        (bind, port), Handler, max_inflight=max_inflight
    )
    server.app = app  # type: ignore[attr-defined]
    return server
