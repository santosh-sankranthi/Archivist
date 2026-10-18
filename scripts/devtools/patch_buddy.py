#!/usr/bin/env python3
"""Patch 7 (buddy): Meta-style orb character + starter chips in the console.

Run from the repo root:
    .venv/bin/python scripts/devtools/patch_buddy.py

Touches: console/src/components/AskRoom.tsx, AskRoom.module.css,
console/src/lib/questions.ts. Idempotent; new files (Orb) are transferred
separately.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CSS_BLOCK = """
/* ---- Buddy character: the orb marks every answer; starter chips drive the
   demo in one click. The gradient lives only here — the rest of the system
   stays Ink & Signal. */
.introOrb {
  margin-bottom: var(--sp-5);
  animation: rise var(--t-slow) var(--ease) both;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2);
  margin-top: var(--sp-5);
  max-width: 520px;
}

.chip {
  font-family: var(--font-ui);
  font-size: 12.5px;
  line-height: 1.4;
  color: var(--text-mid);
  background: rgba(122, 162, 247, 0.06);
  border: 1px solid var(--hairline);
  border-radius: 999px;
  padding: 7px 13px;
  cursor: pointer;
  text-align: left;
  transition:
    color var(--t-fast) var(--ease),
    border-color var(--t-fast) var(--ease),
    background var(--t-fast) var(--ease),
    transform var(--t-fast) var(--ease);
}

.chip:hover:not(:disabled) {
  color: var(--text-hi);
  border-color: var(--hairline-strong);
  background: rgba(122, 162, 247, 0.1);
  transform: translateY(-1px);
}

.chip:disabled {
  opacity: 0.5;
  cursor: default;
}

.buddyAnswer {
  display: flex;
  align-items: flex-start;
  gap: var(--sp-3);
}

.buddyAnswer > *:last-child {
  flex: 1 1 auto;
  min-width: 0;
}
"""

PATCHES: list[tuple[str, str, str, str]] = [
    (
        "b1-import-starters",
        "console/src/components/AskRoom.tsx",
        'import { useSession } from "../lib/session";\n',
        'import { STARTERS } from "../lib/questions";\nimport { useSession } from "../lib/session";\n',
    ),
    (
        "b2-import-orb",
        "console/src/components/AskRoom.tsx",
        'import { GraphCanvas } from "./GraphCanvas";\n',
        'import { GraphCanvas } from "./GraphCanvas";\nimport { Orb } from "./Orb";\n',
    ),
    (
        "b3-running-orb",
        "console/src/components/AskRoom.tsx",
        '              <div className={styles.running} aria-live="polite">\n                <span>Working the graph</span>\n',
        '              <div className={styles.running} aria-live="polite">\n                <Orb size="sm" thinking />\n                <span>Working the graph</span>\n',
    ),
    (
        "b4-intro-orb-chips",
        "console/src/components/AskRoom.tsx",
        '''                  <h1 className={styles.introTitle}>Ask the archive.</h1>
                  <p className={styles.introBody}>
                    One question, one traced path through the graph — every step keeps
                    its Cypher, its timing, and the subgraph it touched.
                  </p>
''',
        '''                  <div className={styles.introOrb}>
                    <Orb size="lg" />
                  </div>
                  <h1 className={styles.introTitle}>Ask the archive.</h1>
                  <p className={styles.introBody}>
                    One question, one traced path through the graph — every step keeps
                    its Cypher, its timing, and the subgraph it touched.
                  </p>
                  <div className={styles.chips}>
                    {STARTERS.map((starter) => (
                      <button
                        key={starter}
                        type="button"
                        className={styles.chip}
                        disabled={busy}
                        onClick={() => void run(starter)}
                      >
                        {starter}
                      </button>
                    ))}
                  </div>
''',
    ),
    (
        "b5-answer-orb",
        "console/src/components/AskRoom.tsx",
        '''                <div className={styles.answer}>
                  <p className={styles.answerText}>{turn.phase.data.answer.text}</p>
                </div>
''',
        '''                <div className={styles.buddyAnswer}>
                  <Orb size="sm" />
                  <div className={styles.answer}>
                    <p className={styles.answerText}>{turn.phase.data.answer.text}</p>
                  </div>
                </div>
''',
    ),
    (
        "b6-css-buddy",
        "console/src/components/AskRoom.module.css",
        "",
        CSS_BLOCK,
    ),
    (
        "b7-starters-copy",
        "console/src/lib/questions.ts",
        '''/** The pinned starter questions — one source for Home and the ⌘K palette,
 *  so the demo script and the UI can't drift apart. These are the questions
 *  the M2 demo pack rehearsed against the real `repo:flask` graph. */
export const STARTERS = [
  "What breaks if flask.helpers.url_for changes?",
  "Who should review a change to src/flask/app.py?",
  "Shortest call path from Flask.wsgi_app to Flask.finalize_request?",
] as const;
''',
        '''/** The pinned starter questions — one source for Home and the ⌘K palette,
 *  so the demo script and the UI can't drift apart. They rehearse against
 *  the live `repo:falkordb-py` graph. */
export const STARTERS = [
  "How do I create a vector index in FalkorDB?",
  "Who calls Graph.query and how deep does the call chain go?",
  "What changed recently in falkordb/graph.py?",
] as const;
''',
    ),
]


def main() -> int:
    failures = 0
    for pid, rel, old, new in PATCHES:
        path = ROOT / rel
        if not path.exists():
            print(f"{pid}: FILE MISSING {rel}")
            failures += 1
            continue
        text = path.read_text(encoding="utf-8")
        if old == "":
            if new in text:
                print(f"{pid}: already applied")
                continue
            if not text.endswith("\n"):
                text += "\n"
            path.write_text(text + new, encoding="utf-8")
            print(f"{pid}: appended {rel}")
            continue
        count = text.count(old)
        if count == 1:
            path.write_text(text.replace(old, new), encoding="utf-8")
            print(f"{pid}: patched {rel}")
        elif count == 0 and new in text:
            print(f"{pid}: already applied")
        else:
            print(f"{pid}: ANCHOR count={count} in {rel} — MANUAL CHECK")
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
