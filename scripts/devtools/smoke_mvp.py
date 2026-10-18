#!/usr/bin/env python3
"""Smoke test the MVP wiring: imports, HMAC, docs tool, graph reads.

Run (on the box): .venv/bin/python scripts/devtools/smoke_mvp.py
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from archivist.gh.config import GhConfig
from archivist.gh.webhook import (
    handle_webhook,
    verify_signature,
)
from archivist.tools import RepoGraph


def main() -> int:
    print("[1] imports ok")
    raw = b'{"zen": 1}'
    sig = "sha256=" + hmac.new(b"secret", raw, hashlib.sha256).hexdigest()
    assert verify_signature("secret", raw, sig), "signature round-trip failed"
    assert not verify_signature("secret", raw + b"x", sig), "tamper undetected"
    print("[2] hmac verify ok")
    cfg = GhConfig(token="x", secret="secret", repos={"a/b": "repo:falkordb-py"})
    ping_headers = {
        "X-Hub-Signature-256": sig,
        "X-GitHub-Event": "ping",
        "X-GitHub-Delivery": "smoke-1",
    }
    status, payload = handle_webhook(ping_headers, raw, cfg)
    assert (status, payload["status"]) == (200, "pong"), (status, payload)
    bad = dict(ping_headers)
    bad["X-Hub-Signature-256"] = "sha256=" + "0" * 64
    status, payload = handle_webhook(bad, raw, cfg)
    assert status == 401, (status, payload)
    print("[3] webhook verify path ok")
    repo = RepoGraph(graph="repo:falkordb-py")
    try:
        out = repo.search_docs("vector index", limit=3)
        print(f"[4] search_docs phrase: status={out['status']} count={out['count']}")
        for row in (out["results"] or [])[:3]:
            print(f"    - {row.get('url')} | {str(row.get('section'))[:60]}")
        out2 = repo.search_docs("how do I merge two nodes", limit=3)
        print(
            f"[5] search_docs fallback: status={out2['status']} "
            f"count={out2['count']}"
        )
        for row in (out2["results"] or [])[:3]:
            print(f"    - {row.get('url')} | {str(row.get('section'))[:60]}")
    finally:
        repo.close()
    keys = [
        "ARCHIVIST_GH_TOKEN",
        "ARCHIVIST_GH_WEBHOOK_SECRET",
        "ARCHIVIST_GH_REPOS",
        "ARCHIVIST_GH_BOT",
        "ARCHIVIST_GH_URL",
    ]
    print("[6] env presence:", {key: bool(os.environ.get(key)) for key in keys})
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
