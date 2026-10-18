"""Slack mention/DM flows: grounded answer posted back in-thread.

Shares the agent runner with the GitHub adapter (same core, same graph,
same memory) — the surfaces differ only in how a question arrives and how
the answer travels back.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request

from archivist.memory import Memory

from ..gh.handlers import _run_agent
from .config import SlackConfig

_log = logging.getLogger("archivist.slack.handlers")

API = "https://slack.com/api"
_MENTION = re.compile(r"<@[A-Z0-9]+>")
FOOTER = (
    "\n\n_🤖 Archivist community agent — grounded in the FalkorDB graph "
    "(code + docs), same memory as the GitHub surface._"
)
_INTRO_QUESTION = (
    "Introduce yourself in two sentences and say what you can help with "
    "in this community."
)


def _post(cfg: SlackConfig, channel: str, text: str, thread_ts: str | None) -> bool:
    body: dict = {"channel": channel, "text": text}
    if thread_ts:
        body["thread_ts"] = thread_ts
    req = urllib.request.Request(
        f"{API}/chat.postMessage",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {cfg.bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
    except (urllib.error.URLError, ValueError) as exc:
        _log.error("chat.postMessage failed: %s", exc)
        return False
    if not data.get("ok"):
        _log.error("chat.postMessage error: %s", data.get("error"))
        return False
    return True


def handle_event(cfg: SlackConfig, event: dict) -> None:
    channel = event.get("channel") or ""
    ts = event.get("ts") or ""
    user = event.get("user") or "someone"
    text = (event.get("text") or "").strip()
    question = _MENTION.sub("", text).strip()
    if not question:
        question = _INTRO_QUESTION
    prompt = (
        "A community member wrote in Slack. Answer from the community "
        f"knowledge graph (code + docs).\n\n{question}"
    )
    try:
        answer = _run_agent(cfg.graph, prompt)
    except Exception as exc:  # noqa: BLE001 - report into the thread instead
        _log.error("slack agent failed: %s", exc)
        _post(
            cfg,
            channel,
            f":warning: I could not build an answer just now ({exc}). "
            "Please try again in a moment.",
            ts,
        )
        return
    memory = Memory(graph=cfg.graph)
    try:
        memory.record_episode(
            kind="slack-answer",
            source="slack",
            channel=channel,
            user=user,
            question=question[:500],
            answer=answer.text[:2000],
        )
    finally:
        memory.close()
    _post(cfg, channel, answer.text.strip() + FOOTER, ts)
    _log.info("slack answered in %s (%s steps)", channel, len(answer.steps))
