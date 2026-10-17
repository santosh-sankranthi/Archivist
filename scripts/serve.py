#!/usr/bin/env python3
"""Serve the Archivist web console — chat + graph explorer + trace panel (M4a).

Usage:
    python scripts/serve.py [--graph repo:flask] [--port 8787] [--bind 127.0.0.1]
    python scripts/serve.py --llm-script docs/demo/m4r/ask-script.json

Flag defaults come from the environment (flags win): FALKORDB_HOST /
FALKORDB_PORT for the FalkorDB endpoint, ARCHIVIST_GRAPH (graph name),
ARCHIVIST_BIND / ARCHIVIST_PORT for the HTTP side — compose.yaml uses
these to point the server at the stack's FalkorDB service.

The LLM status is reported at startup; the chat endpoint stays honest (503)
until ARCHIVIST_LLM_* is configured — or a script is given. ``--llm-script``
starts a *capture/demo* server: the agent loop, tools and graph are real,
but the model replays fixed scenarios from the JSON file (one per ask call,
in order). That is how the Trace Ribbon is captured and reproduced on a box
without a key — a scenario decides *which tools to call*; the graph decides
every number. The script never fabricates results, and the model name shown
in the UI says "scripted (demo)" so nothing pretends otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from archivist.accounts import AccountStore
from archivist.llm import LLMResponse, ToolCall
from archivist.webapp import WebApp, make_server


def _split_demo_chunks(text: str, pieces: int = 5) -> list[str]:
    """Split scripted content into mock-streaming pieces at word boundaries.

    Only the *pacing* is simulated — the text itself is exactly what the
    scenario file says; captures get a visible type-out instead of one blob.
    """
    if len(text) <= 24:
        return [text]
    size = max(1, len(text) // pieces)
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            space = text.find(" ", end)
            if space != -1 and space - end < 24:
                end = space + 1
        chunks.append(text[start:end])
        start = end
    return chunks


class ScriptedLLM:
    """Deterministic stand-in for ``ChatLLM`` (captures + reproducible demos).

    A scenario is claimed lazily, on the first ``chat`` call — the console
    polls ``/api/state`` (which constructs a client for the LLM status chip)
    every 60s, and status probes must not eat scenarios.
    """

    def __init__(self, cursor: dict, scenarios: list[list[dict]]):
        self._cursor = cursor
        self._scenarios = scenarios
        self._script: list[dict] | None = None
        self._ids = 0
        self.model = "scripted (demo)"
        self.usage: dict[str, int] = {}

    def is_configured(self) -> bool:
        return True

    def chat(self, messages, tools=None, on_delta=None):
        if self._script is None:
            index = self._cursor["i"]
            self._script = (
                self._scenarios[index] if index < len(self._scenarios) else []
            )
            self._cursor["i"] = index + 1
        if self._script:
            item = self._script.pop(0)
        else:
            return LLMResponse(
                content="(script exhausted — this capture server replies from a "
                "fixed script; restart it for a fresh run)"
            )
        calls: list[ToolCall] = []
        for raw_call in item.get("tool_calls") or []:
            args = raw_call.get("arguments")
            if not isinstance(args, dict):
                args = {}
            self._ids += 1
            calls.append(
                ToolCall(
                    id=f"scripted-{self._ids}",
                    name=str(raw_call.get("name") or ""),
                    raw_arguments=json.dumps(args),
                    arguments=args,
                )
            )
        content = item.get("content")
        if on_delta is not None and isinstance(content, str) and content:
            chunks = _split_demo_chunks(content)
            for position, chunk in enumerate(chunks):
                on_delta(chunk)
                if position + 1 < len(chunks):
                    time.sleep(0.05)  # visible type-out in captures
        return LLMResponse(content=content, tool_calls=calls)


def scripted_factory(scenarios: list[list[dict]]):
    """Factory for WebApp's ``llm_factory`` seam — one scenario per ask."""
    cursor = {"i": 0}

    def factory() -> ScriptedLLM:
        return ScriptedLLM(cursor, scenarios)

    return factory


def load_scenarios(path: str) -> list[list[dict]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    scenarios = data.get("scenarios") if isinstance(data, dict) else None
    if not isinstance(scenarios, list) or not scenarios:
        raise SystemExit(f'llm script {path}: expected {{"scenarios": [[...], ...]}}')
    return scenarios


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--graph",
        default=os.environ.get("ARCHIVIST_GRAPH", "repo:flask"),
        help="FalkorDB graph name",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("FALKORDB_HOST", "127.0.0.1"),
        help="FalkorDB host",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("FALKORDB_PORT", "6379")),
        help="FalkorDB port",
    )
    parser.add_argument(
        "--bind",
        default=os.environ.get("ARCHIVIST_BIND", "127.0.0.1"),
        help="HTTP bind address",
    )
    parser.add_argument(
        "--http-port",
        type=int,
        default=int(os.environ.get("ARCHIVIST_PORT", "8787")),
        help="HTTP port",
    )
    parser.add_argument(
        "--llm-script",
        metavar="FILE",
        help="JSON scenarios for a deterministic capture server (see module doc)",
    )
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("ARCHIVIST_DATA_DIR", str(ROOT / "data")),
        help="accounts + per-user data root (default <repo>/data)",
    )
    parser.add_argument(
        "--demo-graph",
        default=os.environ.get("ARCHIVIST_DEMO_GRAPH", ""),
        help="shared anonymous graph (default: the --graph value)",
    )
    parser.add_argument(
        "--signup",
        choices=("open", "closed"),
        default=os.environ.get("ARCHIVIST_SIGNUP", "open"),
        help="whether new accounts may be created (default open)",
    )
    parser.add_argument(
        "--max-repos",
        type=int,
        default=int(os.environ.get("ARCHIVIST_MAX_REPOS", "2")),
        help="repos per account (default 2)",
    )
    parser.add_argument(
        "--demo-quota",
        type=int,
        default=int(os.environ.get("ARCHIVIST_DEMO_QUOTA", "40")),
        help="asks per hour per client that may spend the server demo key "
        "(<=0 disables; default 40)",
    )
    args = parser.parse_args()

    factory = None
    if args.llm_script:
        scenarios = load_scenarios(args.llm_script)
        factory = scripted_factory(scenarios)

    accounts = AccountStore(args.data_dir, max_repos=args.max_repos)
    app = WebApp(
        graph=args.graph,
        host=args.host,
        port=args.port,
        llm_factory=factory,
        accounts=accounts,
        demo_graph=args.demo_graph or args.graph,
        signup=args.signup,
        demo_quota=args.demo_quota,
    )
    server = make_server(app, bind=args.bind, port=args.http_port)
    print(f"Archivist console — http://{args.bind}:{server.server_port}")
    print(f"  {app.status_line()}")
    print(
        f"  accounts: data={args.data_dir} signup={app.signup} "
        f"demo={app.demo_graph} max_repos={accounts.max_repos}"
    )
    print(f"  demo quota: {app.demo_quota}/h per client (0 = off)")
    if factory is not None:
        print(f"  capture mode: {len(scenarios)} scripted scenario(s)")
    print("  Ctrl-C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
