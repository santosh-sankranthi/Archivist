"""Web console tests (M4a): static serving, projections, honest refusals.

Integration tests run against a throwaway graph seeded from the chain
fixture (same pattern as test_ingest), so the projections are exercised
for real — the console's contract is "what the graph holds", and that is
what these tests check. Skips cleanly when FalkorDB is absent.
"""

from __future__ import annotations

import gc
import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from itertools import pairwise
from typing import ClassVar

import pytest

from archivist import webapp as webapp_module
from archivist.graph import GraphWriter
from archivist.ingest import ingest
from archivist.llm import LLMResponse
from archivist.memory import Memory
from archivist.webapp import ApiError, WebApp, make_server


@pytest.fixture()
def web_server(chain_repo, falkordb_or_skip, temp_graph):
    """Chain repo ingested into a scratch graph, served on an ephemeral port."""
    host, port = falkordb_or_skip
    ingest(chain_repo, temp_graph, host=host, port=port, rebuild=True)
    app = WebApp(graph=temp_graph, host=host, port=port)
    server = make_server(app, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def get(url: str) -> tuple[int, bytes, str]:
    with urllib.request.urlopen(url, timeout=15) as res:
        return res.status, res.read(), res.headers.get("Content-Type", "")


def get_json(url: str) -> tuple[int, dict]:
    status, body, _ = get(url)
    return status, json.loads(body)


def post_json(url: str, payload: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as res:
        return res.status, json.loads(res.read())


def status_only(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=15) as res:
            return res.status
    except urllib.error.HTTPError as exc:
        return exc.code


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Surface redirects as HTTPError so tests can assert on them directly."""

    def redirect_request(self, *args, **kwargs):
        return None


def test_root_leads_to_console_v1_archived(web_server: str):
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(web_server + "/", timeout=15):
            raise AssertionError("expected a redirect from /")
    except urllib.error.HTTPError as exc:
        assert exc.code == 302
        assert exc.headers.get("Location") == "/console/"

    status, body, content_type = get(web_server + "/classic/")
    assert status == 200
    assert "text/html" in content_type
    assert b"ARCHIVIST" in body


def test_static_assets_served(web_server: str):
    for asset in (
        "/static/style.css",
        "/static/app.js",
        "/static/vendor/cytoscape.min.js",
    ):
        status, body, _ = get(web_server + asset)
        assert status == 200, asset
        assert body, asset


def test_static_refuses_traversal_and_missing():
    app = WebApp()
    # Path traversal is refused at the resolve gate, whatever spelling.
    for rel in (
        "../pyproject.toml",
        "..%2Fpyproject.toml",
        "vendor/../../../../etc/passwd",
    ):
        status, _, _ = app._static(rel)
        assert status == 404, rel
    status, _, _ = app._static("nope.css")
    assert status == 404


# -- Console v2 mount (/console/) --------------------------------------------


def test_console_unbuilt_is_honest_503(web_server: str, tmp_path, monkeypatch):
    """A box without the Vite build must say so, not serve a broken page."""
    monkeypatch.setattr("archivist.webapp.CONSOLE_DIR", tmp_path / "missing")
    assert status_only(web_server + "/console/") == 503
    assert status_only(web_server + "/console") == 503
    try:
        get(web_server + "/console/")
    except urllib.error.HTTPError as exc:
        payload = json.loads(exc.read())
        assert payload["error"] == "unavailable"
        assert "not built" in payload["message"]


def test_console_refuses_traversal(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<!doctype html><div id=root></div>")
    monkeypatch.setattr("archivist.webapp.CONSOLE_DIR", tmp_path)
    app = WebApp()
    for rel in ("../pyproject.toml", "../../etc/passwd", "assets/../../outside.js"):
        with pytest.raises(ApiError) as info:
            app._console(rel)
        assert info.value.status == 404, rel


def test_console_symlinked_dist_serves(tmp_path, monkeypatch):
    """A symlinked ``console/dist`` must serve, not false-404 (REVIEW-M4R W1).

    The containment gate canonicalizes its base before comparing, so
    ``ln -s`` build-dir setups work while escapes through the link are
    still refused.
    """
    real = tmp_path / "real-dist"
    (real / "assets").mkdir(parents=True)
    (real / "index.html").write_text('<!doctype html><div id="root"></div>')
    (real / "assets" / "app.js").write_text("console.log(1)")
    link = tmp_path / "dist"
    link.symlink_to(real)
    monkeypatch.setattr("archivist.webapp.CONSOLE_DIR", link)
    app = WebApp()

    status, content_type, body = app._console("/")
    assert status == 200 and b'id="root"' in body and "text/html" in content_type
    status, _, body = app._console("assets/app.js")
    assert status == 200 and body == b"console.log(1)"

    # Escapes are still refused: the block holds through the symlink too.
    for rel in ("../outside.js", "assets/../../outside.js"):
        with pytest.raises(ApiError) as info:
            app._console(rel)
        assert info.value.status == 404, rel


def test_console_serves_built_app(web_server: str):
    """When console/dist exists: index, assets, SPA fallback, real 404s."""
    from archivist.webapp import CONSOLE_DIR

    if not (CONSOLE_DIR / "index.html").is_file():
        pytest.skip("console/dist is not built on this box (npm run build)")

    status, body, content_type = get(web_server + "/console/")
    assert status == 200
    assert "text/html" in content_type
    assert b'id="root"' in body

    # Both /console and /console/ serve the app shell.
    status, body2, _ = get(web_server + "/console")
    assert status == 200 and body2 == body

    # A client route falls back to the shell; a missing asset does not.
    status, body3, _ = get(web_server + "/console/explorer")
    assert status == 200 and body3 == body
    assert status_only(web_server + "/console/assets/nope.js") == 404

    # The shell references at least one hashed asset that actually serves.
    match = re.search(rb'src="(/console/assets/[^"]+)"', body)
    assert match, "built index should reference a hashed bundle"
    status, asset_body, _ = get(web_server + match.group(1).decode())
    assert status == 200 and asset_body


def test_state_reports_graph_and_counts(web_server: str):
    status, data = get_json(web_server + "/api/state")
    assert status == 200
    assert data["repo"]["name"] == "chain"
    counts = {row["label"]: row["count"] for row in data["nodes"]}
    assert counts["File"] == 2
    assert counts["Symbol"] >= 4  # leaf, helper, main, unused
    assert isinstance(data["llm"]["configured"], bool)
    assert data["memory"]["sessions"] >= 0


def test_file_view_projects_symbols_and_history(web_server: str):
    status, data = get_json(web_server + "/api/graph/file?path=app.py")
    assert status == 200
    kinds = {node["type"] for node in data["nodes"]}
    assert {"File", "Symbol"} <= kinds
    qualnames = {n["props"]["qualname"] for n in data["nodes"] if n["type"] == "Symbol"}
    assert {"main", "unused"} <= qualnames
    assert any(edge["rel"] == "DEFINES" for edge in data["edges"])
    assert data["history"], "expected commits touching app.py"
    assert data["file"]["path"] == "app.py"

    # History is newest-first (regression: it once listed oldest-first).
    ts = [row["ts"] for row in data["history"]]
    assert ts == sorted(ts, reverse=True), ts


def test_activity_lists_recent_commits(web_server: str):
    status, data = get_json(web_server + "/api/graph/activity")
    assert status == 200
    commits = data["commits"]
    assert commits, "the chain repo has commits"
    for row in commits:
        assert set(row) == {"sha", "ts", "summary", "author", "files"}
        assert len(row["sha"]) == 10
        assert isinstance(row["files"], int)
    ts = [row["ts"] for row in commits]
    assert ts == sorted(ts, reverse=True), ts
    assert any(row["files"] >= 1 for row in commits)


def test_path_traces_forward_chain(web_server: str):
    status, data = get_json(
        web_server
        + "/api/graph/path?from_file=app.py&from_qualname=main"
        + "&to_file=util.py&to_qualname=leaf"
    )
    assert status == 200
    assert data["found"] is True
    assert data["direction"] == "forward"
    assert data["hops"] == 2
    qualnames = {n["props"].get("qualname") for n in data["nodes"]}
    assert {"main", "helper", "leaf"} <= qualnames
    assert all(edge["rel"] == "CALLS" for edge in data["edges"])
    assert data["from"] == {"file": "app.py", "qualname": "main"}


def test_path_reports_reverse_direction(web_server: str):
    status, data = get_json(
        web_server
        + "/api/graph/path?from_file=util.py&from_qualname=leaf"
        + "&to_file=app.py&to_qualname=main"
    )
    assert status == 200
    assert data["found"] is True and data["direction"] == "reverse"
    assert data["hops"] == 2
    assert any("to → from" in note for note in data["notes"])


def test_path_absent_is_an_honest_answer(web_server: str):
    status, data = get_json(
        web_server
        + "/api/graph/path?from_file=util.py&from_qualname=leaf"
        + "&to_file=app.py&to_qualname=unused"
    )
    assert status == 200 and data["found"] is False
    assert data["nodes"] == [] and data["edges"] == []
    assert any("no call path" in note for note in data["notes"])
    assert (
        status_only(
            web_server
            + "/api/graph/path?from_file=nope.py&from_qualname=x"
            + "&to_file=app.py&to_qualname=main"
        )
        == 404
    )
    assert (
        status_only(web_server + "/api/graph/path?from_file=a&from_qualname=b") == 400
    )


def test_symbol_view_shows_callers(web_server: str):
    status, data = get_json(
        web_server + "/api/graph/symbol?file=util.py&qualname=helper&depth=1"
    )
    assert status == 200
    focus = [n for n in data["nodes"] if n.get("focus")]
    assert len(focus) == 1 and focus[0]["props"]["qualname"] == "helper"
    assert data["stats"]["callers"] >= 1
    # main -> helper is a resolved call: find the edge and its caller.
    edges = [e for e in data["edges"] if e["rel"] == "CALLS"]
    assert edges, "expected at least one CALLS edge into helper"
    caller_ids = {e["source"] for e in edges}
    callers = [n for n in data["nodes"] if n["id"] in caller_ids]
    assert any(n["props"].get("qualname") == "main" for n in callers)


def test_search_finds_symbols_and_files(web_server: str):
    status, data = get_json(web_server + "/api/graph/find?q=main&limit=10")
    assert status == 200
    assert any(row["qualname"] == "main" for row in data["symbols"])
    status, data = get_json(web_server + "/api/graph/find?q=app&limit=10")
    assert any(row["path"] == "app.py" for row in data["files"])


def test_cypher_escape_hatch_projects_scalars(web_server: str):
    status, data = post_json(
        web_server + "/api/cypher",
        {
            "cypher": "MATCH (s:Symbol {name: $n}) RETURN count(s) AS n",
            "params": {"n": "main"},
        },
    )
    assert status == 200
    assert data["scalars"] == [{"n": 1}]
    assert data["nodes"] == []

    # Empty and overlong queries are refused cleanly, not 500s.
    assert status_only(web_server + "/api/cypher") == 404  # GET not routed
    try:
        post_json(web_server + "/api/cypher", {"cypher": " "})
        raise AssertionError("expected 400")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400


def test_ask_refuses_honestly_without_llm(web_server: str, monkeypatch):
    for var in (
        "ARCHIVIST_LLM_BASE_URL",
        "ARCHIVIST_LLM_API_KEY",
        "ARCHIVIST_LLM_MODEL",
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)
    try:
        post_json(web_server + "/api/ask", {"question": "who calls main?"})
        raise AssertionError("expected 503")
    except urllib.error.HTTPError as exc:
        assert exc.code == 503
        payload = json.loads(exc.read())
        assert payload["error"] == "unavailable"
        assert "no LLM configured" in payload["message"]


def test_cypher_null_scalar_is_preserved(web_server: str):
    # A NULL column is part of what the query returned: keep it visible.
    status, data = post_json(web_server + "/api/cypher", {"cypher": "RETURN null AS z"})
    assert status == 200
    assert data["count"] == 1
    assert data["scalars"] == [{"z": None}]


def test_cypher_refusals_map_to_4xx(web_server: str):
    # A syntax error is a client error, not a server fault.
    try:
        post_json(web_server + "/api/cypher", {"cypher": "MATCH (n RETURN n"})
        raise AssertionError("expected 400")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400
        assert json.loads(exc.read())["error"] == "bad_request"
    # A write through the read-only gate is an explicit 403...
    try:
        post_json(web_server + "/api/cypher", {"cypher": "CREATE (x:ReviewWrite)"})
        raise AssertionError("expected 403")
    except urllib.error.HTTPError as exc:
        assert exc.code == 403
    # ...and nothing was created.
    _, data = post_json(
        web_server + "/api/cypher",
        {"cypher": "MATCH (x:ReviewWrite) RETURN count(x) AS n"},
    )
    assert data["scalars"] == [{"n": 0}]


def test_edge_only_rows_surface_plotting_note(web_server: str):
    status, data = post_json(
        web_server + "/api/cypher", {"cypher": "MATCH (a)-[e]->(b) RETURN e LIMIT 1"}
    )
    assert status == 200
    assert data["count"] == 1
    assert data["edges"] == []
    assert any("include endpoints" in note for note in data["notes"])


def test_cypher_params_must_be_object(web_server: str):
    try:
        post_json(web_server + "/api/cypher", {"cypher": "RETURN 1", "params": []})
        raise AssertionError("expected 400")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400


def test_cap_notes_track_exact_excess(web_server: str, monkeypatch):
    monkeypatch.setattr("archivist.webapp.MAX_SYMBOLS_PER_FILE", 1000)
    _, data = get_json(web_server + "/api/graph/file?path=app.py")
    n = len([x for x in data["nodes"] if x["type"] == "Symbol"])
    assert n >= 2
    assert not any("capped" in note for note in data["notes"])

    # Exactly the cap: nothing is dropped, so no note.
    monkeypatch.setattr("archivist.webapp.MAX_SYMBOLS_PER_FILE", n)
    _, data = get_json(web_server + "/api/graph/file?path=app.py")
    assert not any("capped" in note for note in data["notes"])

    # One under the total: exactly one symbol is not shown.
    monkeypatch.setattr("archivist.webapp.MAX_SYMBOLS_PER_FILE", n - 1)
    _, data = get_json(web_server + "/api/graph/file?path=app.py")
    assert f"symbols capped at {n - 1} (1 not shown)" in data["notes"]

    # Module importers: exactly the cap, nothing dropped.
    monkeypatch.setattr("archivist.webapp.MAX_IMPORTERS_PER_MODULE", 1)
    _, data = get_json(web_server + "/api/graph/module?name=util")
    assert not any("capped" in note for note in data["notes"])


def test_other_methods_return_json_not_html(web_server: str):
    for method in ("HEAD", "PUT", "PATCH", "DELETE", "OPTIONS"):
        request = urllib.request.Request(web_server + "/api/cypher", method=method)
        try:
            urllib.request.urlopen(request, timeout=15)
            raise AssertionError(f"expected 404 for {method}")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404, method
            assert "application/json" in exc.headers.get("Content-Type", ""), method


def test_body_cap_refused_with_413(web_server: str, monkeypatch):
    monkeypatch.setattr("archivist.webapp.MAX_BODY", 64)
    request = urllib.request.Request(
        web_server + "/api/cypher",
        data=b"x" * 100,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=15)
        raise AssertionError("expected 413")
    except urllib.error.HTTPError as exc:
        assert exc.code == 413
        assert json.loads(exc.read())["error"] == "too_large"


def test_server_header_hides_interpreter(web_server: str):
    with urllib.request.urlopen(web_server + "/static/style.css", timeout=15) as res:
        assert res.headers.get("Server") == "Archivist/0.4"


def test_fd_count_stable_across_requests(web_server: str):
    # REVIEW-M4a F1: per-query clients must not accumulate open sockets.
    if not os.path.isdir("/proc/self/fd"):
        pytest.skip("no /proc/self/fd on this host")
    for _ in range(5):  # warm up first-connection fds
        get_json(web_server + "/api/state")
    gc.collect()
    before = len(os.listdir("/proc/self/fd"))
    for i in range(50):
        if i % 2:
            get_json(web_server + "/api/state")
        else:
            get_json(web_server + "/api/graph/file?path=app.py")
    gc.collect()
    after = len(os.listdir("/proc/self/fd"))
    assert after - before <= 6, f"fd leak across 50 requests: {before} -> {after}"


def test_inflight_cap_refuses_excess_with_503(chain_repo, falkordb_or_skip, temp_graph):
    """Under a tunnel, stalled connections must be refused fast, not queued.

    M5 hardening: the console may serve through a public tunnel, where an
    unbounded thread-per-connection server is a flood amplifier. Two stalled
    clients (headers never terminated) hold both slots; a fresh request must
    come back 503 + Retry-After instead of piling on. Closing them frees the
    slots and the console serves again.
    """

    def attempt(url: str) -> tuple[int, str | None]:
        try:
            with urllib.request.urlopen(url, timeout=5) as res:
                return res.status, res.headers.get("Retry-After")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers.get("Retry-After")

    host, port = falkordb_or_skip
    ingest(chain_repo, temp_graph, host=host, port=port, rebuild=True)
    app = WebApp(graph=temp_graph, host=host, port=port)
    server = make_server(app, port=0, max_inflight=2)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    stalled: list[socket.socket] = []
    try:
        for _ in range(2):
            conn = socket.create_connection(
                ("127.0.0.1", server.server_port), timeout=5
            )
            # Send a partial request so the handler blocks mid-headers: the
            # connection holds a slot but never becomes a served request.
            conn.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n")
            stalled.append(conn)
        time.sleep(0.3)  # let the accept loop swallow both before we probe

        deadline = time.monotonic() + 6
        refused = False
        while time.monotonic() < deadline:
            code, retry = attempt(base + "/api/state")
            if code == 503 and retry == "5":
                refused = True
                break
            time.sleep(0.05)
        assert refused, "expected a fast 503 refusal while both slots were held"

        for conn in stalled:
            conn.close()
        stalled.clear()

        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            code, _ = attempt(base + "/api/state")
            if code == 200:
                break
            time.sleep(0.05)
        else:
            pytest.fail("console did not recover after the stalled clients left")
    finally:
        for conn in stalled:
            conn.close()
        server.shutdown()
        server.server_close()


# -- /api/graph/induced (M4R step 3: Trace Ribbon replay) --------------------


def test_induced_resolves_evidence_and_edges(web_server: str):
    """Symbol refs resolve; the CALLS edges *among* them come back."""
    status, data = post_json(
        web_server + "/api/graph/induced",
        {
            "steps": [
                {
                    "id": "s0",
                    "symbols": [
                        {"file": "app.py", "qualname": "main"},
                        {"file": "util.py", "qualname": "helper"},
                        {"file": "util.py", "qualname": "leaf"},
                    ],
                }
            ]
        },
    )
    assert status == 200
    step = data["steps"][0]
    assert step["id"] == "s0"
    coords = {
        (n["props"].get("file"), n["props"].get("qualname")) for n in step["nodes"]
    }
    assert ("app.py", "main") in coords
    assert ("util.py", "helper") in coords
    assert ("util.py", "leaf") in coords
    ids = {
        f"{n['props'].get('file')}:{n['props'].get('qualname')}": n["id"]
        for n in step["nodes"]
    }
    pairs = {(e["source"], e["target"]) for e in step["edges"] if e["rel"] == "CALLS"}
    assert (ids["app.py:main"], ids["util.py:helper"]) in pairs
    assert (ids["util.py:helper"], ids["util.py:leaf"]) in pairs


def test_induced_resolves_commit_refs(web_server: str):
    """History steps replay as a commit → file chain (Gate A W1)."""
    _, file_data = get_json(web_server + "/api/graph/file?path=app.py")
    shas = [row["sha"] for row in file_data["history"][:2]]
    assert len(shas) == 2

    status, data = post_json(
        web_server + "/api/graph/induced",
        {
            "steps": [
                {
                    "id": "h0",
                    "files": [{"path": "app.py"}],
                    "commits": [{"sha": sha} for sha in shas],
                }
            ]
        },
    )
    assert status == 200
    step = data["steps"][0]
    commit_ids = {n["id"] for n in step["nodes"] if n["type"] == "Commit"}
    assert len(commit_ids) == 2
    modified = [e for e in step["edges"] if e["rel"] == "MODIFIED"]
    assert len(modified) == 2
    assert all(e["source"] in commit_ids for e in modified)

    # The fixture history is linear and these are its two newest commits,
    # so the replay must also absorb their PARENT edge (child → parent;
    # seq is git-log position, 0 = newest — so the child has the lower seq).
    paren = [e for e in step["edges"] if e["rel"] == "PARENT"]
    assert len(paren) == 1
    assert paren[0]["source"] in commit_ids and paren[0]["target"] in commit_ids
    by_id = {n["id"]: n for n in step["nodes"] if n["type"] == "Commit"}
    child, parent = by_id[paren[0]["source"]], by_id[paren[0]["target"]]
    assert child["props"]["seq"] < parent["props"]["seq"]

    # Abbreviated shas resolve too (STARTS WITH), for hand-built requests.
    status, data = post_json(
        web_server + "/api/graph/induced",
        {"steps": [{"id": "h1", "commits": [{"sha": shas[0][:8]}]}]},
    )
    assert status == 200
    assert any(n["type"] == "Commit" for n in data["steps"][0]["nodes"])


def test_induced_is_honest_about_missing_refs(web_server: str):
    status, data = post_json(
        web_server + "/api/graph/induced",
        {"steps": [{"id": "s1", "symbols": [{"file": "app.py", "qualname": "ghost"}]}]},
    )
    assert status == 200
    step = data["steps"][0]
    assert step["nodes"] == [] and step["edges"] == []
    assert any("did not resolve" in note for note in step["notes"])


def test_induced_validates_shape(web_server: str):
    for payload in ({}, {"steps": []}, {"steps": [{"symbols": []}]}, {"steps": ["x"]}):
        try:
            post_json(web_server + "/api/graph/induced", payload)
            raise AssertionError(f"expected 400 for {payload!r}")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400, payload


# -- ask via the llm_factory seam (M4R step 3: scripted capture server) ------


class _EchoLLM:
    """Minimal stand-in for ChatLLM: one final answer, no tool calls."""

    model = "fake-model"
    usage: ClassVar[dict] = {}

    def is_configured(self) -> bool:
        return True

    def chat(self, messages, tools=None):
        return LLMResponse(content="scripted answer for the ribbon test")


def test_ask_runs_with_injected_llm_factory(chain_repo, falkordb_or_skip, temp_graph):
    host, port = falkordb_or_skip
    ingest(chain_repo, temp_graph, host=host, port=port, rebuild=True)
    app = WebApp(graph=temp_graph, host=host, port=port, llm_factory=_EchoLLM)
    server = make_server(app, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        status, data = post_json(
            base + "/api/ask", {"question": "who calls main?", "remember": False}
        )
        state_status, state = get_json(base + "/api/state")
    finally:
        server.shutdown()
        server.server_close()
    assert status == 200
    assert data["answer"]["text"] == "scripted answer for the ribbon test"
    assert data["answer"]["llm_calls"] == 1
    assert data["episode_id"] is None  # remember=False -> no memory write
    assert state_status == 200
    assert state["llm"]["configured"] is True
    assert state["llm"]["model"] == "fake-model"


# -- /api/graph/memory (M4R P1: memory timeline) -----------------------------


def _seed_memory(host: str, port: int, graph: str) -> None:
    """Two episodes, one handoff, one fact superseded by a second."""
    mem = Memory(graph=graph, host=host, port=port)
    try:
        mem.record_episode(
            kind="agent-session",
            source="pytest",
            id="ep:first",
            ts=1000,
            question="who calls foo?",
        )
        mem.record_episode(
            kind="agent-session",
            source="pytest",
            id="ep:second",
            ts=2000,
            question="and bar?",
        )
        assert mem.link_handoff("ep:first", "ep:second", note="continue from first")
        mem.remember_fact(
            text="foo is called by bar",
            episode_id="ep:first",
            fact_id="fact:old",
            valid_at=1000,
            refs=[{"kind": "symbol", "file": "util.py", "qualname": "leaf"}],
        )
        superseded = mem.supersede_fact(
            "fact:old",
            text="foo is called by baz",
            episode_id="ep:second",
            fact_id="fact:new",
        )
        assert superseded["supersedes_edge"] is True
    finally:
        mem.close()


def test_memory_now_view_reports_state_and_chains(
    web_server: str, falkordb_or_skip, temp_graph
):
    host, port = falkordb_or_skip
    _seed_memory(host, port, temp_graph)
    status, data = get_json(web_server + "/api/graph/memory")
    assert status == 200
    assert data["mode"] == "now" and data["as_of"] is None
    assert data["counts"] == {
        "episodes": 2,
        "facts": 2,
        "open_facts": 1,
        "handoff_edges": 1,
        "supersede_edges": 1,
    }
    assert data["range"] == {"earliest": 1000, "latest": 2000}
    # "True now": only the open fact is shown...
    assert [f["id"] for f in data["facts"]] == ["fact:new"]
    # ...and the hidden older claim rides along so the chain chip can name it.
    assert [f["id"] for f in data["chain_facts"]] == ["fact:old"]
    assert data["chain_facts"][0]["text"] == "foo is called by bar"
    # Episodes newest first; the handoff note rides on the earlier one.
    assert [e["id"] for e in data["episodes"]] == ["ep:second", "ep:first"]
    first = next(e for e in data["episodes"] if e["id"] == "ep:first")
    second = next(e for e in data["episodes"] if e["id"] == "ep:second")
    assert [h["to"] for h in first["handoffs"]] == ["ep:second"]
    assert first["handoffs"][0]["note"] == "continue from first"
    assert [h["from"] for h in second["handoff_in"]] == ["ep:first"]


def test_memory_history_view_keeps_windows_and_lineage(
    web_server: str, falkordb_or_skip, temp_graph
):
    host, port = falkordb_or_skip
    _seed_memory(host, port, temp_graph)
    status, data = get_json(web_server + "/api/graph/memory?history=1")
    assert status == 200 and data["mode"] == "history"
    by_id = {f["id"]: f for f in data["facts"]}
    assert set(by_id) == {"fact:new", "fact:old"}
    assert by_id["fact:new"]["supersedes"] == ["fact:old"]
    assert by_id["fact:old"]["superseded_by"] == ["fact:new"]
    # Windows tile, exactly as the write path set them.
    assert by_id["fact:new"]["valid_at"] == by_id["fact:old"]["invalid_at"]
    assert by_id["fact:old"]["invalid_at"] is not None
    assert by_id["fact:old"]["refs"] == [{"kind": "Symbol", "label": "util.py:leaf"}]
    assert any("full history" in n for n in data["notes"])
    assert data["chain_facts"] == []  # nothing hidden in this view


def test_memory_as_of_view_is_event_time(web_server: str, falkordb_or_skip, temp_graph):
    host, port = falkordb_or_skip
    _seed_memory(host, port, temp_graph)
    status, data = get_json(web_server + "/api/graph/memory?as_of=1500")
    assert status == 200
    assert data["mode"] == "as_of" and data["as_of"] == 1500
    assert [f["id"] for f in data["facts"]] == ["fact:old"]  # true mid-window
    assert [e["id"] for e in data["episodes"]] == ["ep:first"]  # ts <= T only
    assert any("as of 1500" in n for n in data["notes"])


def test_memory_rejects_bad_as_of(web_server: str):
    assert status_only(web_server + "/api/graph/memory?as_of=nope") == 400
    assert status_only(web_server + "/api/graph/memory?as_of=-5") == 400


def test_memory_empty_graph_is_honest(web_server: str):
    status, data = get_json(web_server + "/api/graph/memory")
    assert status == 200
    assert data["episodes"] == [] and data["facts"] == []
    assert data["counts"]["episodes"] == 0 and data["counts"]["facts"] == 0
    assert any("no memory recorded yet" in n for n in data["notes"])


# -- /api/graph/crew (M4R P1: crew runs) -------------------------------------


def _seed_crew(host: str, port: int, graph: str) -> None:
    """One complete 3-phase run with handoffs; one interrupted planner-only."""
    mem = Memory(graph=graph, host=host, port=port)
    try:
        run = "crew:test:1"
        mem.record_episode(
            kind="agent-session",
            source="pytest",
            id=f"{run}:planner",
            ts=1000,
            question="who calls foo?",
            role="planner",
            run=run,
        )
        mem.record_episode(
            id=f"{run}:planner",
            answer="plan text",
            partial=False,
            steps=1,
            queries=1,
            llm_calls=2,
            duration_ms=10,
        )
        mem.record_episode(
            kind="agent-session",
            source="pytest",
            id=f"{run}:investigator",
            ts=1001,
            question="phase input",
            role="investigator",
            run=run,
        )
        mem.record_episode(
            id=f"{run}:investigator",
            answer="investigation text",
            partial=False,
            steps=2,
            queries=3,
            llm_calls=3,
            duration_ms=20,
        )
        mem.record_episode(
            kind="agent-session",
            source="pytest",
            id=f"{run}:verifier",
            ts=1002,
            question="phase input",
            role="verifier",
            run=run,
        )
        mem.record_episode(
            id=f"{run}:verifier",
            answer="verdict text",
            partial=False,
            steps=2,
            queries=4,
            llm_calls=3,
            duration_ms=30,
        )
        assert mem.link_handoff(f"{run}:planner", f"{run}:investigator", note="go")
        assert mem.link_handoff(f"{run}:investigator", f"{run}:verifier", note="verify")
        # A second run that stopped after planning (crew aborts on failure).
        mem.record_episode(
            kind="agent-session",
            source="pytest",
            id="crew:test:2:planner",
            ts=2000,
            question="second question?",
            role="planner",
            run="crew:test:2",
        )
    finally:
        mem.close()


def test_crew_runs_report_spine_and_handoffs(
    web_server: str, falkordb_or_skip, temp_graph
):
    host, port = falkordb_or_skip
    _seed_crew(host, port, temp_graph)
    status, data = get_json(web_server + "/api/graph/crew")
    assert status == 200
    assert data["counts"] == {"runs": 2, "phases": 4, "handoffs": 2}
    # Newest run first.
    assert [r["run"] for r in data["runs"]] == ["crew:test:2", "crew:test:1"]
    by_run = {r["run"]: r for r in data["runs"]}
    first = by_run["crew:test:1"]
    assert first["status"] == "complete"
    assert [p["role"] for p in first["phases"]] == [
        "planner",
        "investigator",
        "verifier",
    ]
    assert first["question"] == "who calls foo?"
    assert first["started_at"] == 1000 and first["ended_at"] == 1002
    assert first["phases"][2]["answer"] == "verdict text"
    assert first["phases"][1]["queries"] == 3
    assert [h["to"] for h in first["handoffs"]] == ["investigator", "verifier"]
    assert first["handoffs"][0]["note"] == "go"
    second = by_run["crew:test:2"]
    assert second["status"] == "interrupted"
    assert [p["role"] for p in second["phases"]] == ["planner"]


def test_crew_truncates_long_text_and_reports(
    web_server: str, falkordb_or_skip, temp_graph
):
    host, port = falkordb_or_skip
    mem = Memory(graph=temp_graph, host=host, port=port)
    try:
        mem.record_episode(
            kind="agent-session",
            source="pytest",
            id="r:planner",
            ts=1000,
            question="q",
            role="planner",
            run="r",
        )
        mem.record_episode(id="r:planner", answer="x" * 900)
    finally:
        mem.close()
    status, data = get_json(web_server + "/api/graph/crew")
    assert status == 200
    phase = data["runs"][0]["phases"][0]
    assert phase["answer_truncated"] is True
    assert len(phase["answer"]) == 600  # MAX_CREW_ANSWER
    assert any("truncated" in n and "answer" in n for n in data["notes"])


def test_crew_empty_graph_is_honest(web_server: str):
    status, data = get_json(web_server + "/api/graph/crew")
    assert status == 200
    assert data["runs"] == [] and data["counts"]["runs"] == 0
    assert any("no crew runs recorded yet" in n for n in data["notes"])


# -- /api/graph/history (M4R time machine) -----------------------------------


def _commit(
    repo,
    *args: str,
    when: str | None = None,
    name: str = "Dev One",
    email: str = "dev@example.com",
) -> None:
    """One git command with a deterministic identity (mirrors conftest).

    ``when`` overrides author+committer dates (ISO-8601) so tests can place
    commits in specific months — the time machine's buckets are calendar
    months, and a test that never crosses a month boundary cannot see them.
    ``name``/``email`` let ownership tests build real multi-author history.
    """
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
    }
    if when:
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
    subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args],
        check=True,
        capture_output=True,
        env=env,
    )


@pytest.fixture()
def history_server(tmp_path, falkordb_or_skip, temp_graph):
    """A repo with a merge commit and a multi-file commit, served for history.

    The chain fixture is linear; the time machine's honesty hinges on merge
    commits (no diff — kept, zero touches) and on commits that touch several
    files, so this repo carries both.
    """
    host, port = falkordb_or_skip
    repo = tmp_path / "hist"
    repo.mkdir()
    _commit(repo, "init", "-q", "-b", "main")
    (repo / "f.py").write_text("x = 1\n")
    (repo / "g.py").write_text("y = 1\n")
    _commit(repo, "add", ".")
    _commit(repo, "commit", "-q", "-m", "base: f and g")
    _commit(repo, "checkout", "-q", "-b", "side")
    (repo / "h.py").write_text("z = 1\n")
    _commit(repo, "add", ".")
    _commit(repo, "commit", "-q", "-m", "side adds h")
    _commit(repo, "checkout", "-q", "main")
    _commit(repo, "merge", "--no-ff", "-q", "-m", "merge side", "side")
    ingest(repo, temp_graph, host=host, port=port, rebuild=True)
    app = WebApp(graph=temp_graph, host=host, port=port)
    server = make_server(app, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_history_buckets_cover_all_commits(history_server: str):
    status, data = get_json(history_server + "/api/graph/history")
    assert status == 200
    assert data["scope"] == {"file": None}
    totals = data["totals"]
    assert totals["commits"] == 3  # the merge is included — never dropped
    assert totals["added"] == 3 and totals["removed"] == 0
    assert totals["files"] == 3
    assert totals["first_ts"] <= totals["last_ts"]
    buckets = data["buckets"]
    assert sum(b["commits"] for b in buckets) == 3
    assert sum(b["added"] for b in buckets) == 3
    for bucket in buckets:
        assert set(bucket) == {
            "label",
            "t0",
            "t1",
            "commits",
            "touches",
            "added",
            "removed",
        }
    # Buckets tile the calendar: each one ends where the next begins.
    for left, right in pairwise(buckets):
        assert left["t1"] == right["t0"]
    assert any("UTC calendar months" in n for n in data["notes"])


def test_history_window_reports_commits_and_top_files(history_server: str, monkeypatch):
    _, data = get_json(history_server + "/api/graph/history")
    assert data["buckets"], "fixture commits must land in a bucket"
    frm = data["buckets"][0]["t0"]
    to = data["buckets"][-1]["t1"]
    status, win = get_json(history_server + f"/api/graph/history?from={frm}&to={to}")
    assert status == 200
    window = win["window"]
    assert window["from"] == frm and window["to"] == to
    assert len(window["commits"]) == 3
    assert any(c["touches"] == 0 for c in window["commits"])  # the merge stays
    for commit in window["commits"]:
        assert set(commit) == {
            "sha",
            "ts",
            "summary",
            "author",
            "touches",
            "added",
            "removed",
        }
        assert len(commit["sha"]) == 10
    assert {f["path"] for f in window["top_files"]} == {"f.py", "g.py", "h.py"}
    # Caps are exact and reported — no silent trimming.
    monkeypatch.setattr("archivist.webapp.MAX_HISTORY_COMMITS", 2)
    _, win2 = get_json(history_server + f"/api/graph/history?from={frm}&to={to}")
    assert len(win2["window"]["commits"]) == 2
    assert "window commits capped at 2 (1 not shown)" in win2["notes"]
    monkeypatch.setattr("archivist.webapp.MAX_HISTORY_FILES", 2)
    _, win3 = get_json(history_server + f"/api/graph/history?from={frm}&to={to}")
    assert len(win3["window"]["top_files"]) == 2
    assert "top files capped at 2 (1 not shown)" in win3["notes"]


def test_history_file_scope_is_honest(history_server: str):
    _, data = get_json(history_server + "/api/graph/history?file=f.py")
    assert data["scope"] == {"file": "f.py"}
    assert data["totals"]["commits"] == 1
    assert data["totals"]["files"] == 1
    assert sum(b["touches"] for b in data["buckets"]) == 1
    frm = data["buckets"][0]["t0"]
    to = data["buckets"][-1]["t1"]
    _, win = get_json(
        history_server + f"/api/graph/history?file=f.py&from={frm}&to={to}"
    )
    assert len(win["window"]["commits"]) == 1
    assert win["window"]["top_files"] == []
    # A path the graph never saw is a 404, same rule as /api/graph/file.
    assert status_only(history_server + "/api/graph/history?file=nope.py") == 404


def test_history_keeps_commitless_file_honest(
    history_server: str, falkordb_or_skip, temp_graph
):
    host, port = falkordb_or_skip
    writer = GraphWriter(host=host, port=port, graph=temp_graph)
    writer.smoke("CREATE (:File {path: 'ghost.py', lang: 'python', loc: 0})")
    status, data = get_json(history_server + "/api/graph/history?file=ghost.py")
    assert status == 200
    assert data["buckets"] == [] and data["totals"]["commits"] == 0
    assert any("no commits touch" in n for n in data["notes"])


@pytest.fixture()
def gapped_server(tmp_path, falkordb_or_skip, temp_graph):
    """Two commits two months apart — the quiet month must still appear."""
    host, port = falkordb_or_skip
    repo = tmp_path / "gapped"
    repo.mkdir()
    _commit(repo, "init", "-q", "-b", "main")
    (repo / "a.py").write_text("a = 1\n")
    _commit(repo, "add", ".")
    _commit(repo, "commit", "-q", "-m", "january", when="2026-01-15T12:00:00+00:00")
    (repo / "a.py").write_text("a = 2\n")
    _commit(repo, "add", ".")
    _commit(repo, "commit", "-q", "-m", "march", when="2026-03-10T12:00:00+00:00")
    ingest(repo, temp_graph, host=host, port=port, rebuild=True)
    app = WebApp(graph=temp_graph, host=host, port=port)
    server = make_server(app, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_history_fills_quiet_months_with_zeros(gapped_server: str):
    _, data = get_json(gapped_server + "/api/graph/history")
    assert [b["label"] for b in data["buckets"]] == ["2026-01", "2026-02", "2026-03"]
    quiet = data["buckets"][1]
    assert quiet["commits"] == 0 and quiet["added"] == 0 and quiet["touches"] == 0
    assert data["totals"]["commits"] == 2
    assert data["totals"]["added"] == 2
    # Zero-filled months still tile the calendar exactly (scrubber guarantee).
    for left, right in pairwise(data["buckets"]):
        assert left["t1"] == right["t0"]


def test_history_window_validates_params(history_server: str):
    base = history_server + "/api/graph/history"
    assert status_only(base + "?from=100") == 400  # to missing
    assert status_only(base + "?to=100") == 400  # from missing
    assert status_only(base + "?from=200&to=100") == 400  # from >= to
    assert status_only(base + "?from=x&to=y") == 400
    # An empty window is a real answer, not an error.
    status, data = get_json(base + "?from=1&to=2")
    assert status == 200
    assert data["window"]["commits"] == [] and data["window"]["top_files"] == []
    assert any("no commits in this window" in n for n in data["notes"])


# -- Blast radius (/api/graph/impact) ----------------------------------------


@pytest.fixture()
def impact_server(tmp_path, falkordb_or_skip, temp_graph):
    """A three-hop cone plus one caller that lives in a tests/ directory."""
    host, port = falkordb_or_skip
    repo = tmp_path / "impact"
    repo.mkdir()
    (repo / "tests").mkdir()
    _commit(repo, "init", "-q", "-b", "main")
    (repo / "pkg.py").write_text(
        "def leaf():\n"
        "    return 1\n"
        "\n"
        "\n"
        "def middle():\n"
        "    return leaf()\n"
        "\n"
        "\n"
        "def top():\n"
        "    return middle()\n"
    )
    (repo / "tests" / "test_pkg.py").write_text(
        "from pkg import top\n\n\ndef test_top():\n    assert top() == 1\n"
    )
    _commit(repo, "add", ".")
    _commit(repo, "commit", "-q", "-m", "impact fixture")
    ingest(repo, temp_graph, host=host, port=port, rebuild=True)
    app = WebApp(graph=temp_graph, host=host, port=port)
    server = make_server(app, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_impact_walks_transitive_callers_by_level(impact_server: str):
    status, data = get_json(impact_server + "/api/graph/impact?symbol=leaf&depth=4")
    assert status == 200
    assert data["target"]["qualname"] == "leaf"
    assert data["target"]["file"] == "pkg.py"
    # One row per affected symbol at its shortest distance: middle (1),
    # top (2), test_top (3) — never once per path.
    levels = {level["hops"]: level["symbols"] for level in data["levels"]}
    assert [level["hops"] for level in data["levels"]] == [1, 2, 3]
    assert [row["qualname"] for row in levels[1]] == ["middle"]
    assert [row["qualname"] for row in levels[2]] == ["top"]
    assert [row["qualname"] for row in levels[3]] == ["test_top"]
    # Chains start at the caller and end at the target (drawable as edges).
    chain = levels[3][0]["chain"]
    assert [node["qualname"] for node in chain] == [
        "test_top",
        "top",
        "middle",
        "leaf",
    ]
    summary = data["summary"]
    assert summary == {
        "direct_callers": 1,
        "transitive_callers": 3,
        "affected_files": 2,
        "affected_test_files": 1,
        "max_hops": 3,
        "shown": 3,
        "truncated": False,
        "walk_capped": False,
    }
    files = {row["path"]: row for row in data["files"]}
    assert files["pkg.py"] == {
        "path": "pkg.py",
        "symbols": 2,
        "min_hops": 1,
        "tests": False,
    }
    assert files["tests/test_pkg.py"]["tests"] is True
    assert any("path heuristic" in note for note in data["notes"])


def test_impact_depth_limit_and_honest_caps(impact_server: str, monkeypatch):
    _, shallow = get_json(impact_server + "/api/graph/impact?symbol=leaf&depth=1")
    assert shallow["summary"]["transitive_callers"] == 1
    assert [level["hops"] for level in shallow["levels"]] == [1]

    # A limit cut is disclosed with exact counts, never silent.
    _, capped = get_json(impact_server + "/api/graph/impact?symbol=leaf&limit=2")
    assert capped["summary"]["shown"] == 2
    assert capped["summary"]["truncated"] is True
    assert capped["summary"]["transitive_callers"] == 3
    assert "showing 2 of 3 affected symbols (limit=2)" in capped["notes"]


def test_impact_entry_points_are_a_real_answer(impact_server: str):
    status, data = get_json(impact_server + "/api/graph/impact?symbol=test_top")
    assert status == 200
    assert data["levels"] == []
    assert data["summary"]["transitive_callers"] == 0
    assert any("entry point" in note for note in data["notes"])


def test_impact_refuses_bad_or_ambiguous_symbols(impact_server: str):
    base = impact_server + "/api/graph/impact"
    assert status_only(base) == 400  # symbol missing
    assert status_only(base + "?symbol=nope") == 404
    assert status_only(base + "?symbol=leaf&depth=0") == 400
    assert status_only(base + "?symbol=leaf&depth=x") == 400
    assert status_only(base + "?symbol=leaf&limit=0") == 400


# -- Ownership (/api/graph/ownership) ----------------------------------------


@pytest.fixture()
def ownership_server(tmp_path, falkordb_or_skip, temp_graph):
    """Three authors: a shared file (2:1), a solo file (1), a bus-factor file.

    ``c.py`` carries six commits by one author, which is what the single-owner
    rule looks for; ``a.py`` is shared by Alice and Bob so its shares exercise
    the exact-fraction path.
    """
    host, port = falkordb_or_skip
    repo = tmp_path / "own"
    repo.mkdir()
    _commit(repo, "init", "-q", "-b", "main")
    (repo / "a.py").write_text("def dup():\n    return 1\n\ndef a():\n    return 2\n")
    _commit(repo, "add", "a.py")
    _commit(
        repo,
        "commit",
        "-q",
        "-m",
        "alice adds a",
        when="2024-01-05T12:00:00+00:00",
        name="Alice",
        email="alice@example.com",
    )
    (repo / "b.py").write_text("def dup():\n    return 3\n\ndef b():\n    return 4\n")
    _commit(repo, "add", "b.py")
    _commit(
        repo,
        "commit",
        "-q",
        "-m",
        "bob adds b",
        when="2024-02-05T12:00:00+00:00",
        name="Bob",
        email="bob@example.com",
    )
    (repo / "a.py").write_text(
        "def dup():\n    return 1\n\ndef a():\n    return 2\n\n\ndef a2():\n    return 5\n"
    )
    _commit(repo, "add", "a.py")
    _commit(
        repo,
        "commit",
        "-q",
        "-m",
        "alice extends a",
        when="2024-03-05T12:00:00+00:00",
        name="Alice",
        email="alice@example.com",
    )
    (repo / "a.py").write_text(
        "def dup():\n    return 1\n\ndef a():\n    return 2\n\n\ndef a2():\n"
        "    return 5\n\n\ndef a3():\n    return 6\n"
    )
    _commit(repo, "add", "a.py")
    _commit(
        repo,
        "commit",
        "-q",
        "-m",
        "bob touches a",
        when="2024-04-05T12:00:00+00:00",
        name="Bob",
        email="bob@example.com",
    )
    (repo / "c.py").write_text("def c():\n    return 0\n")
    _commit(repo, "add", "c.py")
    _commit(
        repo,
        "commit",
        "-q",
        "-m",
        "carol adds c",
        when="2024-05-05T12:00:00+00:00",
        name="Carol",
        email="carol@example.com",
    )
    for index in range(5):
        with (repo / "c.py").open("a") as handle:
            handle.write(f"\ndef c{index}():\n    return {index}\n")
        _commit(repo, "add", "c.py")
        _commit(
            repo,
            "commit",
            "-q",
            "-m",
            f"carol edits c{index}",
            when=f"2024-{6 + index:02d}-05T12:00:00+00:00",
            name="Carol",
            email="carol@example.com",
        )
    ingest(repo, temp_graph, host=host, port=port, rebuild=True)
    app = WebApp(graph=temp_graph, host=host, port=port)
    server = make_server(app, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_ownership_repo_ranks_people_and_names_single_owner_files(
    ownership_server: str,
):
    status, data = get_json(ownership_server + "/api/graph/ownership")
    assert status == 200
    assert data["scope"] == "repo"
    assert data["totals"] == {
        "commits": 10,
        "commits_with_diff": 10,
        "files": 3,
        "authors": 3,
        "file_changes": 10,
    }
    # Carol has six diff-bearing commits; Bob's last touch (April) outranks
    # Alice's (March) on the tie-break, never on commit count.
    assert [person["name"] for person in data["people"]] == [
        "Carol",
        "Bob",
        "Alice",
    ]
    assert data["people"][0]["share"] == 0.6
    assert data["hot_files"][0]["path"] == "c.py"
    assert data["hot_files"][0]["commits"] == 6
    assert data["hot_files"][0]["owners"][0]["share"] == 1.0
    # c.py is the only file past the six-commit bar with one owner.
    assert [risk["path"] for risk in data["risks"]] == ["c.py"]
    assert data["risks"][0]["primary_share"] == 1.0
    assert any("no CODEOWNERS" in note for note in data["notes"])


def test_ownership_file_reports_exact_shares_and_recent_history(
    ownership_server: str,
):
    status, data = get_json(ownership_server + "/api/graph/ownership?file=a.py")
    assert status == 200
    assert data["scope"] == "file"
    assert data["file"]["commits"] == 3
    assert data["file"]["authors"] == 2
    owners = {owner["email"]: owner for owner in data["owners"]}
    assert owners["alice@example.com"]["commits"] == 2
    assert owners["alice@example.com"]["share"] == 0.667
    assert owners["bob@example.com"]["share"] == 0.333
    assert data["recent_commits"][0]["author"] == "Bob"
    assert data["recent_commits"][0]["summary"] == "bob touches a"
    assert data["reviewers"][0]["name"] == "Alice"
    assert "commit(s)" in data["reviewers"][0]["why"]
    assert data["recent_total"] == 3
    assert any("not policy" in note for note in data["notes"])


def test_ownership_resolves_symbols_and_refuses_ambiguity(ownership_server: str):
    base = ownership_server + "/api/graph/ownership"
    status, data = get_json(base + "?symbol=a")
    assert status == 200 and data["path"] == "a.py"
    # `dup` is defined in both files: no silent pick, a scoped answer or 400.
    assert status_only(base + "?symbol=dup") == 400
    status, scoped = get_json(base + "?symbol=dup&file=b.py")
    assert status == 200 and scoped["path"] == "b.py"
    assert status_only(base + "?file=nope.py") == 404
    assert status_only(base + "?symbol=nope") == 404


def test_ownership_caps_are_disclosed(ownership_server: str, monkeypatch):
    monkeypatch.setattr(webapp_module, "MAX_OWNERS", 1)
    _, data = get_json(ownership_server + "/api/graph/ownership")
    assert len(data["people"]) == 1
    assert any("people capped at 1 (2 more not shown" in n for n in data["notes"])

    monkeypatch.setattr(webapp_module, "MAX_OWNERS", 1)
    _, file_data = get_json(ownership_server + "/api/graph/ownership?file=a.py")
    assert len(file_data["owners"]) == 1
    assert any("owners capped at 1 (1 more not shown" in n for n in file_data["notes"])
