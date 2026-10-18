#!/usr/bin/env python3
"""Record the repo owner's first-seen marker (avoids a self-welcome).

The console file-issue flow authors issues with the bot account; without
this marker the agent would welcome its own account on the first filede
issue.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from archivist.memory import Memory


def main() -> int:
    memory = Memory(graph="repo:falkordb-py")
    try:
        memory.record_episode(
            id="gh-first-seen:santosh-sankranthi/falkordb-py:santosh-sankranthi",
            kind="gh-first-seen",
            source="github",
            author="santosh-sankranthi",
            repo="santosh-sankranthi/falkordb-py",
        )
    finally:
        memory.close()
    print("first-seen recorded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
