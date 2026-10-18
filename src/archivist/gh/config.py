"""GitHub adapter configuration — environment-driven, fail-soft.

Env:
    ARCHIVIST_GH_TOKEN            classic PAT (repo scope)
    ARCHIVIST_GH_WEBHOOK_SECRET   webhook HMAC secret
    ARCHIVIST_GH_REPOS            comma list of ``owner/repo=graph`` mappings
    ARCHIVIST_GH_BOT              the bot's GitHub login (self-ignore)
    ARCHIVIST_GH_URL              public base URL (welcome links, webhook)
    ARCHIVIST_GH_DRY              "1" = never write to GitHub (dry run)
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int_env(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name) or "").strip() or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class GhConfig:
    """Everything a webhook worker needs, resolved once per request."""

    token: str = ""
    secret: str = ""
    repos: dict[str, str] | None = None
    bot: str = ""
    url: str = ""
    dry_run: bool = False
    workers: int = 4
    cooldown_s: int = 90
    daily_cap: int = 50

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.secret and self.repos)

    def graph_for(self, slug: str) -> str | None:
        return (self.repos or {}).get(slug.lower())


def load_config() -> GhConfig:
    repos: dict[str, str] = {}
    for chunk in (os.environ.get("ARCHIVIST_GH_REPOS") or "").split(","):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        slug, _, graph = chunk.partition("=")
        if slug.strip() and graph.strip():
            repos[slug.strip().lower()] = graph.strip()
    return GhConfig(
        token=(os.environ.get("ARCHIVIST_GH_TOKEN") or "").strip(),
        secret=(os.environ.get("ARCHIVIST_GH_WEBHOOK_SECRET") or "").strip(),
        repos=repos,
        bot=(os.environ.get("ARCHIVIST_GH_BOT") or "").strip().lower(),
        url=(os.environ.get("ARCHIVIST_GH_URL") or "").strip().rstrip("/"),
        dry_run=(os.environ.get("ARCHIVIST_GH_DRY") or "").strip() == "1",
        workers=_int_env("ARCHIVIST_GH_WORKERS", 4),
        cooldown_s=_int_env("ARCHIVIST_GH_COOLDOWN", 90),
        daily_cap=_int_env("ARCHIVIST_GH_DAILY_CAP", 50),
    )
