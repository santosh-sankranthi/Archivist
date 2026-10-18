"""Web → GitHub: file a console answer as an issue on the mapped fork.

The issue is created with the bot identity; the resulting ``issues`` webhook
then lets the hosted agent answer it on the thread — closing the loop from
the browser to the repository and into shared memory.
"""

from __future__ import annotations

from .client import GhError, GitHub
from .config import load_config


def file_issue(question: str, answer: str = "") -> dict:
    """Create the issue; returns ``{repo, number, url}`` or raises GhError."""
    cfg = load_config()
    if not cfg.enabled:
        raise GhError("GitHub surface not configured on this server")
    slug = next(iter(cfg.repos), "")
    if not slug:
        raise GhError("no repository mapped (ARCHIVIST_GH_REPOS is empty)")
    title = " ".join(question.split())[:120] or "Question from the Archivist console"
    lines = [
        "_Filed from the Archivist console by a community member._",
        "",
        "**Question**",
        question.strip(),
    ]
    excerpt = " ".join(answer.split())
    if excerpt:
        lines += [
            "",
            "**Console answer (excerpt)**",
            excerpt[:1500],
        ]
    lines += [
        "",
        f"<!-- archivist:file-issue graph={cfg.graph_for(slug)} -->",
    ]
    gh = GitHub(cfg.token, dry_run=cfg.dry_run)
    issue = gh.create_issue(slug, title, "\n".join(lines))
    return {
        "repo": slug,
        "number": issue.get("number"),
        "url": issue.get("html_url"),
    }
