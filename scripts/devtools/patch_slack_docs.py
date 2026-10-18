#!/usr/bin/env python3
"""Add the Slack workspace invite link to judge-facing docs."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LINK = (
    "https://join.slack.com/t/demoworkspace-gam4651/shared_invite/"
    "zt-4dwhz22o0-5JfcMZ_qpBTN7aigAsvvkQ"
)

PATCHES = [
    (
        "judges-slack-link",
        "JUDGES.md",
        """**3. Slack surface:** mention `@archivist` in the community workspace —
answers arrive in-thread and are stored in the same memory.
""",
        f"""**3. Slack surface:** the workspace is open — join with
{LINK}
then mention `@archivist` in a channel: answers arrive in-thread and
are stored in the same memory.
""",
    ),
    (
        "submission-slack-link",
        "SUBMISSION.md",
        """2. **Slack agent** — the same core behind the Events API: `@archivist`
   mention → in-thread answer, written to the same memory.
""",
        f"""2. **Slack agent** — the same core behind the Events API: `@archivist`
   mention → in-thread answer, written to the same memory. Workspace
   (open invite): {LINK}
""",
    ),
]


def main() -> int:
    failures = 0
    for pid, rel, old, new in PATCHES:
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        if new in text:
            print(f"{pid}: already applied")
            continue
        count = text.count(old)
        if count == 1:
            path.write_text(text.replace(old, new), encoding="utf-8")
            print(f"{pid}: patched {rel}")
        else:
            print(f"{pid}: ANCHOR count={count} in {rel} — MANUAL CHECK")
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
