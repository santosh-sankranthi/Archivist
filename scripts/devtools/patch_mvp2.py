#!/usr/bin/env python3
"""Patch 2: stopword filter for the search_docs keyword fallback.

Run from the repo root:
    .venv/bin/python scripts/devtools/patch_mvp2.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "src/archivist/tools.py"

OLD_ANCHOR = '''SymbolId = tuple[str, str]  # (file, qualname) — the graph's symbol identity
'''

NEW_ANCHOR = '''SymbolId = tuple[str, str]  # (file, qualname) — the graph's symbol identity

# Generic English words that would otherwise swamp keyword doc search.
_DOC_STOPWORDS = frozenset(
    {
        "and", "about", "can", "does", "for", "from", "get", "here", "how",
        "into", "its", "make", "need", "see", "set", "that", "the", "their",
        "them", "there", "they", "this", "use", "using", "want", "what",
        "when", "where", "which", "why", "with", "you", "your",
    }
)
'''

OLD_WORDS = '''            words = [word for word in cleaned.split() if len(word) > 2][:3]
'''

NEW_WORDS = '''            words = [
                word
                for word in cleaned.split()
                if len(word) > 2 and word.lower() not in _DOC_STOPWORDS
            ][:3]
'''


def _patch(path: Path, old: str, new: str, label: str) -> bool:
    text = path.read_text(encoding="utf-8")
    if old in text:
        if text.count(old) != 1:
            print(f"{label}: ANCHOR count={text.count(old)} — MANUAL CHECK")
            return False
        path.write_text(text.replace(old, new), encoding="utf-8")
        print(f"{label}: patched")
        return True
    if new in text:
        print(f"{label}: already applied")
        return True
    print(f"{label}: ANCHOR MISSING")
    return False


def main() -> int:
    ok = _patch(TOOLS, OLD_ANCHOR, NEW_ANCHOR, "p2a-stopwords-const")
    ok = _patch(TOOLS, OLD_WORDS, NEW_WORDS, "p2b-stopword-filter") and ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
