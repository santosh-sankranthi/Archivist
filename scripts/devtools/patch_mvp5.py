#!/usr/bin/env python3
"""Patch 5: Slack Events API route on the webapp.

Run from the repo root:
    .venv/bin/python scripts/devtools/patch_mvp5.py
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEBAPP = ROOT / "src/archivist/webapp.py"

PATCHES: list[tuple[str, str, str]] = [
    (
        "w6-slack-route",
        '''            elif method == "POST":
                if path == "/github/webhook":
                    return self._github_webhook(ctx)
                if path == "/api/auth/signup":
''',
        '''            elif method == "POST":
                if path == "/github/webhook":
                    return self._github_webhook(ctx)
                if path == "/slack/events":
                    return self._slack_events(ctx)
                if path == "/api/auth/signup":
''',
    ),
    (
        "w7-slack-method",
        "    def _github_webhook(self, ctx: RequestContext) -> tuple[int, str, bytes]:",
        '''    def _slack_events(self, ctx: RequestContext) -> tuple[int, str, bytes]:
        """Additive route: verify + enqueue one Slack Events API request."""
        from .slack.events import handle_request

        headers = dict(ctx.headers) if ctx.headers else {}
        status, payload = handle_request(headers, ctx.raw_body)
        return self._json(status, payload)

    def _github_webhook(self, ctx: RequestContext) -> tuple[int, str, bytes]:''',
    ),
]


def main() -> int:
    text = WEBAPP.read_text(encoding="utf-8")
    failures = 0
    for pid, old, new in PATCHES:
        if old in text:
            if text.count(old) != 1:
                print(f"{pid}: ANCHOR count={text.count(old)} — MANUAL CHECK")
                failures += 1
                continue
            text = text.replace(old, new)
            print(f"{pid}: patched")
        elif new in text:
            print(f"{pid}: already applied")
        else:
            print(f"{pid}: ANCHOR MISSING")
            failures += 1
    if not failures:
        WEBAPP.write_text(text, encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
