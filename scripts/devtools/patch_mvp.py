#!/usr/bin/env python3
"""Surgical MVP patches: GitHub webhook route + docs tool + agent schema.

Run from the repo root:
    .venv/bin/python scripts/devtools/patch_mvp.py            # report only
    .venv/bin/python scripts/devtools/patch_mvp.py --apply    # write patches

Backups of every touched file land in /tmp/mvp-bak/ (first run only).
Idempotent: after a successful --apply, re-runs report "already applied".
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BAK = Path("/tmp/mvp-bak")

PATCHES: list[tuple[str, str, str, str]] = [
    (
        "w1-raw-body-field",
        "src/archivist/webapp.py",
        '''    user: User | None = None
    token: str | None = None
    headers: dict = field(default_factory=dict)
    client_ip: str = "unknown"
    secure: bool = False
''',
        '''    user: User | None = None
    token: str | None = None
    headers: dict = field(default_factory=dict)
    client_ip: str = "unknown"
    secure: bool = False
    raw_body: bytes = b""
''',
    ),
    (
        "w2-raw-init",
        "src/archivist/webapp.py",
        '''                body: dict = {}
                if method == "POST":
''',
        '''                body: dict = {}
                raw = b""
                if method == "POST":
''',
    ),
    (
        "w3-ctx-raw-body",
        "src/archivist/webapp.py",
        '''                ctx = RequestContext(
                    user=session.user if session else None,
                    token=token,
                    headers=self.headers,
                    client_ip=str(self.client_address[0]),
                    secure=(self.headers.get("X-Forwarded-Proto") or "").lower()
                    == "https",
                )
''',
        '''                ctx = RequestContext(
                    user=session.user if session else None,
                    token=token,
                    headers=self.headers,
                    client_ip=str(self.client_address[0]),
                    secure=(self.headers.get("X-Forwarded-Proto") or "").lower()
                    == "https",
                    raw_body=raw,
                )
''',
    ),
    (
        "w4-webhook-route",
        "src/archivist/webapp.py",
        '''            elif method == "POST":
                if path == "/api/auth/signup":
''',
        '''            elif method == "POST":
                if path == "/github/webhook":
                    return self._github_webhook(ctx)
                if path == "/api/auth/signup":
''',
    ),
    (
        "w5-webhook-method",
        "src/archivist/webapp.py",
        "    def _run_ask(",
        '''    def _github_webhook(self, ctx: RequestContext) -> tuple[int, str, bytes]:
        """Additive route: verify + enqueue one GitHub delivery, ack fast.

        The adapter lives in ``archivist.gh``; this method only hands over
        the raw body (HMAC) and headers. GitHub times out at 10 s, so the
        agent itself runs on the adapter's worker pool.
        """
        from .gh.webhook import handle_webhook

        headers = dict(ctx.headers) if ctx.headers else {}
        status, payload = handle_webhook(headers, ctx.raw_body)
        return self._json(status, payload)

    def _run_ask(''',
    ),
    (
        "a1-search-docs-schema",
        "src/archivist/agent.py",
        '''    _fn(
        "query",
        "Read-only Cypher escape hatch for questions the curated tools do not fit. Writes are rejected by the database.",
''',
        '''    _fn(
        "search_docs",
        "Search the ingested FalkorDB documentation (Cypher reference, clients, configuration, guides) and get citable sections with their docs.falkordb.com URLs. Use for questions about how to use FalkorDB or its features.",
        {
            "query": {
                "type": "string",
                "description": "Topic or question, e.g. 'vector index', 'MERGE semantics', 'connection pooling'.",
            },
            "limit": {
                "type": "integer",
                "description": "Max sections to return (default 8, max 20).",
            },
        },
        ["query"],
    ),
    _fn(
        "query",
        "Read-only Cypher escape hatch for questions the curated tools do not fit. Writes are rejected by the database.",
''',
    ),
    (
        "a2-prompt-mention",
        "src/archivist/agent.py",
        '''  `search_commits` finds the "why" behind changes; `find_symbols` locates
  definitions. `query` is a read-only Cypher escape hatch for anything else.
''',
        '''  `search_commits` finds the "why" behind changes; `find_symbols` locates
  definitions; `search_docs` surfaces the ingested FalkorDB documentation
  (Cypher reference, clients, configuration). `query` is a read-only
  Cypher escape hatch for anything else.
''',
    ),
    (
        "t1-search-docs-tool",
        "src/archivist/tools.py",
        "    def query(self, cypher: str, params: dict | None = None, limit: int = 100) -> dict:",
        '''    def search_docs(self, query: str, limit: int = 8) -> dict:
        """Search the ingested documentation (``DocPage`` nodes) by topic.

        Tries the query as a phrase first; if nothing matches, the most
        distinctive words are searched individually. Returns sections with
        their public ``docs.falkordb.com`` URLs, ready to cite.
        """
        since = self._mark()
        limit = min(_clamp_limit(limit), 20)
        query = (query or "").strip()
        if not query:
            return self._envelope(
                "search_docs", "bad_request", {}, [], ["empty query"], since
            )
        rows = self._q(
            """
            MATCH (d:DocPage)
            WHERE toLower(d.title) CONTAINS toLower($q)
               OR toLower(d.section) CONTAINS toLower($q)
               OR toLower(d.text) CONTAINS toLower($q)
            RETURN d.url AS url, d.title AS title, d.section AS section,
                   substring(d.text, 0, 600) AS excerpt
            ORDER BY CASE WHEN toLower(d.title) CONTAINS toLower($q) THEN 0
                          WHEN toLower(d.section) CONTAINS toLower($q) THEN 1
                          ELSE 2 END
            LIMIT $limit
            """,
            {"q": query, "limit": limit},
        )
        notes: list[str] = []
        if rows:
            notes = ["phrase match"]
        else:
            cleaned = query.translate(str.maketrans("", "", ".,:;?!()[]{}'\\"*"))
            words = [word for word in cleaned.split() if len(word) > 2][:3]
            if words:
                where = " OR ".join(
                    f"toLower(d.text) CONTAINS toLower($w{i})"
                    for i in range(len(words))
                )
                params: dict = {f"w{i}": word for i, word in enumerate(words)}
                params["limit"] = limit
                rows = self._q(
                    f"""
                    MATCH (d:DocPage)
                    WHERE {where}
                    RETURN d.url AS url, d.title AS title,
                           d.section AS section,
                           substring(d.text, 0, 600) AS excerpt
                    LIMIT $limit
                    """,
                    params,
                )
                if rows:
                    notes = [f"keyword match: {', '.join(words)}"]
        if rows:
            status = "ok"
        else:
            status = "not_found"
            notes = [
                "no documentation matches — say the docs likely do not "
                "cover this instead of guessing"
            ]
        return self._envelope(
            "search_docs",
            status,
            {"query": query, "limit": limit},
            rows,
            notes,
            since,
        )

    def query(self, cypher: str, params: dict | None = None, limit: int = 100) -> dict:''',
    ),
]


def _backup(rel: str) -> None:
    src = ROOT / rel
    BAK.mkdir(parents=True, exist_ok=True)
    dest = BAK / rel.replace("/", "__")
    if src.exists() and not dest.exists():
        shutil.copy2(src, dest)


def main() -> int:
    apply = "--apply" in sys.argv[1:]
    failures = 0
    for pid, rel, old, new in PATCHES:
        path = ROOT / rel
        if not path.exists():
            print(f"{pid}: FILE MISSING {rel}")
            failures += 1
            continue
        text = path.read_text(encoding="utf-8")
        count = text.count(old)
        if count == 1:
            if apply:
                _backup(rel)
                path.write_text(text.replace(old, new), encoding="utf-8")
            print(f"{pid}: {'patched' if apply else 'ready'} {rel}")
        elif count == 0 and new in text:
            print(f"{pid}: already applied")
        else:
            print(f"{pid}: ANCHOR count={count} in {rel} — MANUAL CHECK NEEDED")
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
