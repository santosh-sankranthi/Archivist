"""Web console tests (M4a): static serving, projections, honest refusals.

Integration tests run against a throwaway graph seeded from the chain
fixture (same pattern as test_ingest), so the projections are exercised
for real — the console's contract is "what the graph holds", and that is
what these tests check. Skips cleanly when FalkorDB is absent.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from archivist.ingest import ingest
from archivist.webapp import WebApp, make_server


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


def test_static_assets_served(web_server: str):
    status, body, content_type = get(web_server + "/")
    assert status == 200
    assert "text/html" in content_type
    assert b"ARCHIVIST" in body

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
        assert "no LLM configured" in payload["message"]
