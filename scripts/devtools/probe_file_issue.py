#!/usr/bin/env python3
"""POST /api/gh/file-issue — file a question as an issue on the mapped fork.

Usage:
  .venv/bin/python scripts/devtools/probe_file_issue.py Does FalkorDB support hybrid search?
  .venv/bin/python scripts/devtools/probe_file_issue.py --url http://127.0.0.1:8787 q words here
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="*")
    parser.add_argument("--answer", default="")
    parser.add_argument("--url", default="http://127.0.0.1:8787")
    args = parser.parse_args()
    question = " ".join(args.question).strip()
    if not question:
        print("usage: probe_file_issue.py <question words...>")
        return 1
    payload = {"question": question, "answer": args.answer}
    req = urllib.request.Request(
        f"{args.url}/api/gh/file-issue",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:400]}")
        return 1
    except OSError as exc:
        print(f"request failed: {exc}")
        return 1
    print(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
