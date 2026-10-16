#!/usr/bin/env python3
"""M4a demo — the web console's JSON layer, end to end, against repo:flask.

Calls ``WebApp.dispatch`` directly (the same routing the HTTP server uses)
so the transcript is deterministic and needs no open port; every number
below is a real read against the live graph. Regenerates
``docs/demo/m4a-console.txt``.

Note: ``/api/ask`` is exercised with no key configured on this box, which
shows the console's honest 503 path; with ARCHIVIST_LLM_* set it runs the
same agent loop as ``scripts/ask.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from archivist.webapp import WebApp


def show(title: str, status: int, payload: dict, keys: list[str] | None = None) -> None:
    print(f"$ {title}")
    print(f"  -> HTTP {status}")
    if keys:
        for key in keys:
            value = payload.get(key)
            print(f"  {key}: {json.dumps(value, default=str)[:240]}")
    print()


def main() -> None:
    app = WebApp()
    print("M4a — Archivist web console: JSON layer over repo:flask (live graph)")
    print()

    status, _, raw = app.dispatch("GET", "/api/state", {}, {})
    state = json.loads(raw)
    counts = {c["label"]: c["count"] for c in state["nodes"]}
    print("$ GET /api/state")
    print(
        f"  -> HTTP {status} · repo={state['repo']['name']} "
        f"head={state['repo']['head'][:10]}"
    )
    print(f"  nodes: {counts}")
    print(
        f"  memory: {state['memory']['sessions']} sessions, "
        f"{state['memory']['facts']} facts · llm configured: "
        f"{state['llm']['configured']}"
    )
    print()

    status, _, raw = app.dispatch("GET", "/api/graph/overview", {}, {})
    overview = json.loads(raw)
    print("$ GET /api/graph/overview")
    print(f"  -> HTTP {status} · hubs the explorer opens with")
    for hub in overview["hubs"]["symbols"][:3]:
        print(
            f"     symbol {hub['qualname']:<22} {hub['callers']} callers ({hub['file']})"
        )
    for mod in overview["hubs"]["modules"][:3]:
        print(
            f"     module {mod['name']:<22} {mod['importers']} importers, {mod['sites']} sites"
        )
    print()

    status, _, raw = app.dispatch("GET", "/api/graph/find", {"q": ["url_for"]}, {})
    found = json.loads(raw)
    print("$ GET /api/graph/find?q=url_for")
    print(f"  -> HTTP {status} · {len(found['symbols'])} symbol hit(s)")
    for s in found["symbols"]:
        print(f"     {s['file']}:{s['lineno']}  {s['qualname']}")
    print()

    status, _, raw = app.dispatch(
        "GET", "/api/graph/file", {"path": ["src/flask/helpers.py"]}, {}
    )
    file_view = json.loads(raw)
    print("$ GET /api/graph/file?path=src/flask/helpers.py")
    print(
        f"  -> HTTP {status} · {len(file_view['nodes'])} nodes, "
        f"{len(file_view['edges'])} edges · {file_view['file']['loc']} loc"
    )
    for h in file_view["history"][:3]:
        print(f"     {h['sha']}  +{h['added']}/-{h['removed']}  {h['summary'][:54]}")
    print()

    status, _, raw = app.dispatch(
        "GET",
        "/api/graph/symbol",
        {"file": ["src/flask/helpers.py"], "qualname": ["url_for"], "depth": ["1"]},
        {},
    )
    symbol_view = json.loads(raw)
    focus = next(n for n in symbol_view["nodes"] if n.get("focus"))
    print("$ GET /api/graph/symbol?file=src/flask/helpers.py&qualname=url_for&depth=1")
    print(
        f"  -> HTTP {status} · {len(symbol_view['nodes'])} nodes, "
        f"{len(symbol_view['edges'])} edges · focus={focus['props']['qualname']}"
    )
    print(
        f"  stats: {symbol_view['stats']['callers']} callers / "
        f"{symbol_view['stats']['callees']} callees (direct, strong)"
    )
    notes = symbol_view["notes"] or ["(none)"]
    print(f"  notes: {'; '.join(notes)}")
    print()

    status, _, raw = app.dispatch(
        "POST",
        "/api/cypher",
        {},
        {
            "cypher": "MATCH (c:Symbol)-[e:CALLS]->(s:Symbol) "
            "WHERE e.weak = false WITH s, count(DISTINCT c) AS fan_in "
            "RETURN s.qualname AS symbol, s.file AS file, fan_in "
            "ORDER BY fan_in DESC LIMIT 5",
            "params": {},
        },
    )
    agg = json.loads(raw)
    print("$ POST /api/cypher  (escape hatch: highest fan-in symbols)")
    print(f"  -> HTTP {status} · {agg['count']} row(s)")
    for row in agg["scalars"]:
        print(f"     {row['fan_in']:>3} callers  {row['symbol']}  ({row['file']})")
    print()

    status, _, raw = app.dispatch(
        "POST", "/api/ask", {}, {"question": "who calls url_for?"}
    )
    refusal = json.loads(raw)
    print("$ POST /api/ask  (no key on this box — honest refusal, not a fake answer)")
    print(f"  -> HTTP {status}")
    print(f"  {refusal['message'][:170]}…")
    print()
    print("note: every number above is read back from the live repo:flask graph;")
    print("the console renders exactly these payloads — nothing is staged.")


if __name__ == "__main__":
    main()
