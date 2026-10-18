#!/usr/bin/env python3
"""Patch 4: allow bot-authored ISSUES through (comments stay self-ignored).

Run from the repo root:
    .venv/bin/python scripts/devtools/patch_mvp4.py
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HANDLERS = ROOT / "src/archivist/gh/handlers.py"

OLD = '''    body = (issue.get("body") or "")[:4000]
    if _skip_author(cfg, login):
        return
'''

NEW = '''    body = (issue.get("body") or "")[:4000]
    if not login or login.endswith("[bot]"):
        # Self-authored issues are allowed (web file-issue + seeding); only
        # other bots are ignored. Self-COMMENTS stay ignored (no loops).
        return
'''


def main() -> int:
    text = HANDLERS.read_text(encoding="utf-8")
    if OLD in text:
        if text.count(OLD) != 1:
            print(f"ANCHOR count={text.count(OLD)} — MANUAL CHECK")
            return 1
        HANDLERS.write_text(text.replace(OLD, NEW), encoding="utf-8")
        print("self-issue-guard: patched")
        return 0
    if "Self-authored issues are allowed" in text:
        print("self-issue-guard: already applied")
        return 0
    print("self-issue-guard: ANCHOR MISSING")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
