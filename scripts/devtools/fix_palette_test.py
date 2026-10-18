#!/usr/bin/env python3
"""Point the palette ranking test at a FalkorDB starter query."""

from __future__ import annotations

from pathlib import Path

P = Path("/home/openclaw/projects/archivist/console/src/lib/palette.test.ts")
OLD = 'buildPaletteItems("who should review app.py", null);'
NEW = 'buildPaletteItems("who calls graph.query", null);'


def main() -> int:
    text = P.read_text(encoding="utf-8")
    if NEW in text:
        print("already patched")
        return 0
    if OLD not in text:
        print("anchor missing — MANUAL CHECK")
        return 1
    P.write_text(text.replace(OLD, NEW), encoding="utf-8")
    print("palette test patched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
