#!/usr/bin/env python3
"""Make replay_issue.py work for issues beyond the seed pack (fetch from GitHub)."""

from __future__ import annotations

from pathlib import Path

P = Path("/home/openclaw/projects/archivist/scripts/devtools/replay_issue.py")
OLD = "    title, body = seed_demo.ISSUES[args.number - 1]\n"
NEW = (
    "    if 1 <= args.number <= len(seed_demo.ISSUES):\n"
    "        title, body = seed_demo.ISSUES[args.number - 1]\n"
    "    else:\n"
    "        _code, issue = gh._request(\"GET\", f\"/repos/{slug}/issues/{args.number}\")\n"
    "        title = (issue or {}).get(\"title\") or \"\"\n"
    "        body = (issue or {}).get(\"body\") or \"\"\n"
)


def main() -> int:
    text = P.read_text(encoding="utf-8")
    if NEW in text:
        print("already patched")
        return 0
    if OLD not in text:
        print("anchor missing — MANUAL CHECK")
        return 1
    P.write_text(text.replace(OLD, NEW), encoding="utf-8")
    print("replay_issue.py patched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
