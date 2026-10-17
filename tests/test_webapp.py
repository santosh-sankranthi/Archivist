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
import threading
import time
import urllib.error
import urllib.request
from typing import ClassVar

import pytest

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
