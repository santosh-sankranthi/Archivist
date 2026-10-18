#!/usr/bin/env python3
"""Patch 3: wrap the not_found notes string for ruff ISC004.

Run from the repo root:
    .venv/bin/python scripts/devtools/patch_mvp3.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "src/archivist/tools.py"

OLD = '''            notes = [
                "no documentation matches — say the docs likely do not "
                "cover this instead of guessing"
            ]
'''

NEW = '''            notes = [
                (
                    "no documentation matches — say the docs likely do not "
                    "cover this instead of guessing"
                )
            ]
'''


def main() -> int:
    text = TOOLS.read_text(encoding="utf-8")
    if OLD in text:
        if text.count(OLD) != 1:
            print(f"ANCHOR count={text.count(OLD)} — MANUAL CHECK")
            return 1
        TOOLS.write_text(text.replace(OLD, NEW), encoding="utf-8")
        print("isc004-fix: patched")
        return 0
    if NEW in text:
        print("isc004-fix: already applied")
        return 0
    print("isc004-fix: ANCHOR MISSING")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
