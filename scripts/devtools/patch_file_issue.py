#!/usr/bin/env python3
"""Patch 8 (file-issue): web -> GitHub action. Console answers can be filed
as real issues on the mapped fork, where the hosted agent answers them.

Run from the repo root:
    .venv/bin/python scripts/devtools/patch_file_issue.py

Touches: src/archivist/webapp.py (route + method), console AskRoom.tsx +
module.css (button), JUDGES.md + SUBMISSION.md (copy). The server module
archivist/gh/filing.py is transferred separately. Idempotent.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

WEBAPP_METHOD = '''    def _gh_file_issue(self, body: dict) -> dict:
        """Additive route: file a console answer as a GitHub issue (web -> act).

        The issue lands on the mapped demo repo under the bot identity; the
        resulting webhook lets the hosted agent answer it on the thread, so
        the console can *act* on GitHub, not just read from it.
        """
        from .gh.client import GhError
        from .gh.filing import file_issue

        question = str(body.get("question") or "").strip()
        answer = str(body.get("answer") or "")[:4000]
        if not question:
            raise ApiError(400, "question is required", code="bad_request")
        if len(question) > 2000:
            raise ApiError(400, "question is too long", code="bad_request")
        try:
            filed = file_issue(question, answer)
        except GhError as exc:
            raise ApiError(
                503, f"GitHub surface unavailable: {exc}", code="gh_unavailable"
            ) from exc
        return {"filed": True, **filed}

'''

CONSOLE_TYPES = '''type FiledState =
  | { kind: "idle" }
  | { kind: "busy" }
  | { kind: "done"; number: number; url: string }
  | { kind: "error"; message: string };

/** POST /api/gh/file-issue response (web -> GitHub action). */
interface FileIssueResponse {
  filed: boolean;
  repo: string;
  number: number;
  url: string;
}

'''

CONSOLE_FILE_FN = '''  async function fileIssue(
    turnId: number,
    question: string,
    answer: string,
  ): Promise<void> {
    setTurns((prev) =>
      prev.map((turn) =>
        turn.id === turnId ? { ...turn, filed: { kind: "busy" } } : turn,
      ),
    );
    try {
      const response = await postJSON<FileIssueResponse>("/api/gh/file-issue", {
        question,
        answer,
      });
      setTurns((prev) =>
        prev.map((turn) =>
          turn.id === turnId
            ? {
                ...turn,
                filed: { kind: "done", number: response.number, url: response.url },
              }
            : turn,
        ),
      );
    } catch (error) {
      const message = error instanceof ApiError ? error.message : String(error);
      setTurns((prev) =>
        prev.map((turn) =>
          turn.id === turnId ? { ...turn, filed: { kind: "error", message } } : turn,
        ),
      );
    }
  }

'''

CONSOLE_FILE_ROW = '''                <div className={styles.fileRow}>
                  {turn.filed.kind === "done" ? (
                    <a
                      className={styles.filedLink}
                      href={turn.filed.url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      filed as #{turn.filed.number} on GitHub ↗
                    </a>
                  ) : (
                    <button
                      type="button"
                      className={styles.fileBtn}
                      disabled={turn.filed.kind === "busy"}
                      onClick={() => {
                        if (turn.phase.kind !== "done") return;
                        void fileIssue(turn.id, turn.question, turn.phase.data.answer.text);
                      }}
                    >
                      {turn.filed.kind === "busy" ? "filing…" : "File as GitHub issue"}
                    </button>
                  )}
                  {turn.filed.kind === "error" ? (
                    <span className={styles.fileError}>{turn.filed.message}</span>
                  ) : null}
                </div>
'''

CSS_BLOCK = """
/* ---- Web -> GitHub: file any answer as an issue (the buddy *acts*). */
.fileRow {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-2);
  margin-top: var(--sp-2);
}

.fileBtn {
  font-family: var(--font-ui);
  font-size: 12px;
  font-weight: 500;
  color: var(--text-mid);
  background: transparent;
  border: 1px solid var(--hairline);
  border-radius: 999px;
  padding: 5px 12px;
  cursor: pointer;
  transition:
    color var(--t-fast) var(--ease),
    border-color var(--t-fast) var(--ease),
    background var(--t-fast) var(--ease);
}

.fileBtn:hover:not(:disabled) {
  color: var(--text-hi);
  border-color: var(--hairline-strong);
  background: rgba(122, 162, 247, 0.08);
}

.fileBtn:disabled {
  opacity: 0.55;
  cursor: default;
}

.filedLink {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-hi);
  text-decoration: none;
}

.filedLink:hover {
  text-decoration: underline;
}

.fileError {
  font-size: 12px;
  font-style: italic;
  color: #d98a8a;
}
"""

PATCHES = [
    (
        "w8-file-issue-route",
        "src/archivist/webapp.py",
        '''                if path == "/slack/events":
                    return self._slack_events(ctx)
                if path == "/api/auth/signup":
''',
        '''                if path == "/slack/events":
                    return self._slack_events(ctx)
                if path == "/api/gh/file-issue":
                    return self._json(200, self._gh_file_issue(body))
                if path == "/api/auth/signup":
''',
    ),
    (
        "w9-file-issue-method",
        "src/archivist/webapp.py",
        '''    def _slack_events(self, ctx: RequestContext) -> tuple[int, str, bytes]:
        """Additive route: verify + enqueue one Slack Events API request."""
''',
        WEBAPP_METHOD
        + '''    def _slack_events(self, ctx: RequestContext) -> tuple[int, str, bytes]:
        """Additive route: verify + enqueue one Slack Events API request."""
''',
    ),
    (
        "c1-file-types",
        "console/src/components/AskRoom.tsx",
        '''interface Props {
  variant: "stage" | "panel";
''',
        CONSOLE_TYPES
        + '''interface Props {
  variant: "stage" | "panel";
''',
    ),
    (
        "c2-turn-field",
        "console/src/components/AskRoom.tsx",
        '''  phase: Phase;
  replay: ReplayBundle | null;
  replayError: string | null;
}
''',
        '''  phase: Phase;
  replay: ReplayBundle | null;
  replayError: string | null;
  filed: FiledState;
}
''',
    ),
    (
        "c3-run-init",
        "console/src/components/AskRoom.tsx",
        '''        phase: { kind: "running" },
        replay: null,
        replayError: null,
      },
''',
        '''        phase: { kind: "running" },
        replay: null,
        replayError: null,
        filed: { kind: "idle" },
      },
''',
    ),
    (
        "c4-file-fn",
        "console/src/components/AskRoom.tsx",
        '''  function submit(event: FormEvent): void {
''',
        CONSOLE_FILE_FN
        + '''  function submit(event: FormEvent): void {
''',
    ),
    (
        "c5-file-row",
        "console/src/components/AskRoom.tsx",
        '''                      : "not remembered"}
                  </span>
                </div>
''',
        '''                      : "not remembered"}
                  </span>
                </div>
'''
        + CONSOLE_FILE_ROW,
    ),
    (
        "c6-css-file-issue",
        "console/src/components/AskRoom.module.css",
        "",
        CSS_BLOCK,
    ),
    (
        "j1-judges-file-issue",
        "JUDGES.md",
        '''Ask the same class of question in the console. Anonymous visits are
quota-limited so the demo stays fair for everyone.
''',
        '''Ask the same class of question in the console. Anonymous visits are
quota-limited so the demo stays fair for everyone.

Then press **"File as GitHub issue"** on any answer — the question lands
on the fork as a real issue and the hosted agent replies on the thread:
the web surface *acting* on the repository.
''',
    ),
    (
        "j2-submission-built",
        "SUBMISSION.md",
        '''3. **Web buddy** — the deployed console: ask the same brain from the
   browser, instantly.
''',
        '''3. **Web buddy** — the deployed console: ask the same brain from the
   browser, instantly — and file any answer as a GitHub issue in one
   click, which the hosted agent then answers on the thread. The web
   surface *acts* on the repo, not just reads it.
''',
    ),
    (
        "j3-submission-roadmap",
        "SUBMISSION.md",
        '''embeddings, a weekly community brief, richer web→GitHub action hand-offs,
multi-repo communities.
''',
        '''embeddings, a weekly community brief, richer action hand-offs (labels and
assignments from the console), multi-repo communities.
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
