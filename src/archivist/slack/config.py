"""Slack adapter configuration — environment-driven, fail-soft.

Env:
    ARCHIVIST_SLACK_BOT_TOKEN        xoxb-... (chat.postMessage)
    ARCHIVIST_SLACK_SIGNING_SECRET   Events API signing secret
    ARCHIVIST_SLACK_CHANNELS         optional comma allowlist of channel ids
    ARCHIVIST_SLACK_GRAPH            graph the Slack surface reads
                                     (default repo:falkordb-py)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SlackConfig:
    bot_token: str = ""
    signing_secret: str = ""
    channels: tuple[str, ...] = field(default_factory=tuple)
    graph: str = "repo:falkordb-py"

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.signing_secret)

    def channel_ok(self, channel: str) -> bool:
        return not self.channels or channel in self.channels


def load_config() -> SlackConfig:
    channels = tuple(
        part.strip()
        for part in (os.environ.get("ARCHIVIST_SLACK_CHANNELS") or "").split(",")
        if part.strip()
    )
    return SlackConfig(
        bot_token=(os.environ.get("ARCHIVIST_SLACK_BOT_TOKEN") or "").strip(),
        signing_secret=(
            os.environ.get("ARCHIVIST_SLACK_SIGNING_SECRET") or ""
        ).strip(),
        channels=channels,
        graph=(
            os.environ.get("ARCHIVIST_SLACK_GRAPH") or "repo:falkordb-py"
        ).strip(),
    )
