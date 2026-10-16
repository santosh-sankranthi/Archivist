#!/usr/bin/env python3
"""Serve the Archivist web console — chat + graph explorer + trace panel (M4a).

Usage:
    python scripts/serve.py [--graph repo:flask] [--port 8787] [--bind 127.0.0.1]

The LLM status is reported at startup; the chat endpoint stays honest (503)
until ARCHIVIST_LLM_* is configured, while the explorer works regardless.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from archivist.webapp import WebApp, make_server


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--graph", default="repo:flask", help="FalkorDB graph name")
    parser.add_argument("--host", default="127.0.0.1", help="FalkorDB host")
    parser.add_argument("--port", type=int, default=6379, help="FalkorDB port")
    parser.add_argument("--bind", default="127.0.0.1", help="HTTP bind address")
    parser.add_argument("--http-port", type=int, default=8787, help="HTTP port")
    args = parser.parse_args()

    app = WebApp(graph=args.graph, host=args.host, port=args.port)
    server = make_server(app, bind=args.bind, port=args.http_port)
    print(f"Archivist console — http://{args.bind}:{server.server_port}")
    print(f"  {app.status_line()}")
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
