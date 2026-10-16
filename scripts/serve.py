#!/usr/bin/env python3
"""Serve the Archivist web console — chat + graph explorer + trace panel (M4a).

Usage:
    python scripts/serve.py [--graph repo:flask] [--port 8787] [--bind 127.0.0.1]
    python scripts/serve.py --llm-script docs/demo/m4r/ask-script.json

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
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from archivist.llm import LLMResponse, ToolCall
from archivist.webapp import WebApp, make_server


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

    def chat(self, messages, tools=None):
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
        return LLMResponse(content=item.get("content"), tool_calls=calls)


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
    parser.add_argument("--graph", default="repo:flask", help="FalkorDB graph name")
    parser.add_argument("--host", default="127.0.0.1", help="FalkorDB host")
    parser.add_argument("--port", type=int, default=6379, help="FalkorDB port")
    parser.add_argument("--bind", default="127.0.0.1", help="HTTP bind address")
    parser.add_argument("--http-port", type=int, default=8787, help="HTTP port")
    parser.add_argument(
        "--llm-script",
        metavar="FILE",
        help="JSON scenarios for a deterministic capture server (see module doc)",
    )
    args = parser.parse_args()

    factory = None
    if args.llm_script:
        scenarios = load_scenarios(args.llm_script)
        factory = scripted_factory(scenarios)

    app = WebApp(graph=args.graph, host=args.host, port=args.port, llm_factory=factory)
    server = make_server(app, bind=args.bind, port=args.http_port)
    print(f"Archivist console — http://{args.bind}:{server.server_port}")
    print(f"  {app.status_line()}")
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
