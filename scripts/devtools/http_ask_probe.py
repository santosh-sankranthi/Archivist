#!/usr/bin/env python3
"""Probe the running console's /api/ask endpoint from inside the box.

Usage: python http_ask_probe.py [words of the question...]
Prints HTTP status + first 3k of the JSON response. Read-only except that
the server (by design) records the ask as a memory episode.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8787"
question = " ".join(sys.argv[1:]) or "who calls url_for?"

body = json.dumps({"question": question}).encode("utf-8")
req = urllib.request.Request(
    f"{BASE}/api/ask",
    data=body,
    headers={"content-type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=300) as resp:
        payload = resp.read()
        print(f"HTTP {resp.status}")
        print(payload[:3000].decode("utf-8", "replace"))
except urllib.error.HTTPError as exc:
    print(f"HTTP {exc.code}")
    print(exc.read()[:3000].decode("utf-8", "replace"))
except Exception as exc:  # noqa: BLE001
    print(f"ERR {type(exc).__name__}: {exc}")
