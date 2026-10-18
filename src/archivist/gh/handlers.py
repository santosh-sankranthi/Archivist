"""Issue + comment flows for the community agent.

One agent run per issue/mention, grounded in the community graph (code +
docs), posted back as a single comment: answer, related issues, (optional)
welcome, signed footer. Heuristics handle labeling and the security
redirect — no extra LLM calls beyond the answer itself.
"""

from __future__ import annotations

import logging
import re
import time
from collections import deque

from archivist.actions import Actions
from archivist.agent import Agent
from archivist.llm import ChatLLM
from archivist.memory import Memory
from archivist.tools import RepoGraph

from .client import GhError, GitHub
from .config import GhConfig

_log = logging.getLogger("archivist.gh.handlers")

_BOT_NAMES = {"archivist", "archivist-agent", "archivist-community-agent"}
_SECURITY_HINTS = (
    "vulnerability",
    "security issue",
    "cve-",
    "exploit",
    "sql injection",
    "rce",
    "remote code execution",
    "privilege escalation",
)
_QUESTION_STARTS = ("how", "what", "why", "where", "when", "does", "can", "is")
_BUG_HINTS = ("crash", "traceback", "error", "stack trace", "fails", "broken")
_FEATURE_HINTS = ("feature request", "please add", "support for", "proposal")
_NULL_ANSWERS = {
    "",
    "(no answer produced)",
    "(no answer produced — the client disconnected)",
}

_recent: dict[str, deque] = {}
_per_day: dict[str, int] = {}
_day: dict[str, int] = {}

FOOTER = (
    "\n\n---\n"
    "🤖 _Archivist community agent — built from this repo's code graph and "
    "the FalkorDB docs, every claim linked. I can be wrong; human "
    "maintainers decide._"
)

SECURITY_REPLY = (
    "Thanks for flagging this. Security reports don't belong in public "
    "issues — please report it privately through the repository's "
    "**Security → Report a vulnerability** flow and the maintainers will "
    "take it from there. 🙏"
)


def _mentions_bot(cfg: GhConfig, text: str) -> bool:
    lowered = text.lower()
    if cfg.bot and f"@{cfg.bot}" in lowered:
        return True
    return any(f"@{name}" in lowered for name in _BOT_NAMES)


def _skip_author(cfg: GhConfig, login: str) -> bool:
    login = (login or "").lower()
    return not login or login == cfg.bot or login.endswith("[bot]")


def _cooldown_ok(cfg: GhConfig, login: str, now: float) -> bool:
    window = _recent.setdefault(login, deque(maxlen=8))
    while window and now - window[0] > cfg.cooldown_s:
        window.popleft()
    if window:
        return False
    day = int(now // 86400)
    if _day.get(login) != day:
        _day[login] = day
        _per_day[login] = 0
    if _per_day.get(login, 0) >= cfg.daily_cap:
        return False
    window.append(now)
    _per_day[login] = _per_day.get(login, 0) + 1
    return True


def _classify(title: str, body: str) -> list[str]:
    text = f"{title}\n{body}".lower()
    labels: list[str] = []
    if title.strip().endswith("?") or title.lower().startswith(_QUESTION_STARTS):
        labels.append("question")
    elif any(hint in text for hint in _BUG_HINTS):
        labels.append("bug")
    elif any(hint in text for hint in _FEATURE_HINTS):
        labels.append("enhancement")
    if labels == ["bug"]:
        has_version = bool(re.search(r"\b\d+\.\d+", text))
        has_repro = any(
            key in text
            for key in ("steps to reproduce", "reproduce", "repro:", "traceback")
        )
        if not (has_version and has_repro):
            labels.append("needs-info")
    return labels


def _needs_info_note(labels: list[str]) -> str:
    if "needs-info" not in labels:
        return ""
    return (
        "\n\n_To help me route this faster: please add the version you're on, "
        "your Python/environment, and the exact steps (or traceback) that "
        "show the problem._"
    )


def _run_agent(graph: str, question: str, *, attempts: int = 2):
    """One grounded answer from the community graph (retried once if empty)."""
    llm = ChatLLM()
    if not llm.is_configured():
        raise GhError("LLM not configured on the server")
    last: str = "unknown"
    for attempt in range(attempts):
        repo = RepoGraph(graph=graph)
        memory = Memory(graph=graph)
        actions = Actions(graph=graph)
        try:
            answer = Agent(
                repo,
                llm,
                max_steps=8,
                memory=memory,
                actions=actions,
            ).ask(question)
        except Exception as exc:  # noqa: BLE001 - report + retry, never raise
            last = f"{type(exc).__name__}: {exc}"
            _log.warning("agent run failed (attempt %s): %s", attempt + 1, exc)
            continue
        finally:
            repo.close()
            memory.close()
            actions.close()
        text = (answer.text or "").strip()
        if (
            text
            and text not in _NULL_ANSWERS
            and not text.startswith("(LLM failure")
        ):
            return answer
        last = text or answer.error or "empty"
        _log.warning(
            "agent produced no answer (attempt %s): %s", attempt + 1, last[:120]
        )
    raise GhError(f"agent produced no answer ({last})")


def _welcome_note(
    cfg: GhConfig, repo_obj: RepoGraph, slug: str, login: str
) -> str:
    key = f"gh-first-seen:{slug}:{login}"
    rows = repo_obj.query(
        "MATCH (e:Episode {id: $id}) RETURN e.id AS id", {"id": key}
    )["results"]
    if rows:
        return ""
    base = f"https://github.com/{slug}"
    note = (
        f"\n\n👋 Welcome, @{login} — first one from you here! "
        f"Helpful links: [contributing guide]({base}/blob/main/CONTRIBUTING.md) · "
        f"[good first issues]({base}/labels/good%20first%20issue) · "
        f"[docs](https://docs.falkordb.com)."
    )
    if cfg.url:
        note += (
            f" You can also ask me live at {cfg.url} — same memory, "
            "instant answers."
        )
    return note


def _related_note(gh: GitHub, slug: str, number: int, title: str) -> str:
    words = [word for word in re.split(r"\W+", title) if len(word) > 3][:3]
    if not words:
        return ""
    try:
        hits = gh.search_issues(slug, " ".join(words), limit=3)
    except GhError as exc:
        _log.warning("related search failed: %s", exc)
        return ""
    lines = []
    for item in hits:
        if item.get("number") in (number, None):
            continue
        lines.append(
            f"- possibly related: [#{item['number']} {item.get('title', '')}]"
            f"({item.get('html_url', '')})"
        )
        if len(lines) >= 2:
            break
    if not lines:
        return ""
    return "\n\n**Related in this repo:**\n" + "\n".join(lines)


def handle_event(cfg: GhConfig, event: str, payload: dict, graph: str) -> None:
    gh = GitHub(cfg.token, dry_run=cfg.dry_run)
    slug = (payload.get("repository") or {}).get("full_name") or ""
    issue = payload.get("issue") or {}
    number = issue.get("number")
    if not slug or not number:
        return
    if event == "issues":
        _on_issue(cfg, gh, slug, issue, graph)
    elif event == "issue_comment":
        comment = payload.get("comment") or {}
        _on_comment(cfg, gh, slug, issue, comment, graph)


def _on_issue(
    cfg: GhConfig, gh: GitHub, slug: str, issue: dict, graph: str
) -> None:
    login = (issue.get("user") or {}).get("login") or ""
    title = issue.get("title") or ""
    body = (issue.get("body") or "")[:4000]
    if _skip_author(cfg, login):
        return
    now = time.time()
    if not _cooldown_ok(cfg, login, now):
        _log.info("cooldown: skipping %s#%s from %s", slug, issue["number"], login)
        return
    text = f"{title}\n\n{body}"
    if any(hint in text.lower() for hint in _SECURITY_HINTS):
        gh.post_comment(slug, issue["number"], SECURITY_REPLY)
        return
    labels = _classify(title, body)
    question = (
        "A community member opened a GitHub issue. Answer it from the "
        f"community knowledge graph (code + docs).\n\nTitle: {title}\n\n{body}"
    )
    try:
        answer = _run_agent(graph, question)
    except GhError as exc:
        _log.error("no answer for %s#%s: %s", slug, issue["number"], exc)
        return
    parts = [answer.text.strip(), _related_note(gh, slug, issue["number"], title)]
    repo_obj = RepoGraph(graph=graph)
    memory = Memory(graph=graph)
    try:
        welcome = _welcome_note(cfg, repo_obj, slug, login)
        if welcome:
            parts.append(welcome)
            memory.record_episode(
                id=f"gh-first-seen:{slug}:{login}",
                kind="gh-first-seen",
                source="github",
                author=login,
                repo=slug,
            )
        memory.record_episode(
            kind="github-answer",
            source="github",
            question=title,
            answer=answer.text[:2000],
            url=issue.get("html_url"),
            repo=slug,
            author=login,
            steps=len(answer.steps),
        )
    finally:
        repo_obj.close()
        memory.close()
    parts.append(_needs_info_note(labels))
    parts.append(FOOTER)
    gh.post_comment(slug, issue["number"], "".join(p for p in parts if p))
    gh.add_labels(slug, issue["number"], labels)
    _log.info(
        "answered %s#%s (labels=%s, steps=%s)",
        slug,
        issue["number"],
        labels,
        len(answer.steps),
    )


def _on_comment(
    cfg: GhConfig,
    gh: GitHub,
    slug: str,
    issue: dict,
    comment: dict,
    graph: str,
) -> None:
    login = (comment.get("user") or {}).get("login") or ""
    body = (comment.get("body") or "").strip()
    if _skip_author(cfg, login) or not body:
        return
    if not _mentions_bot(cfg, body):
        return
    now = time.time()
    if not _cooldown_ok(cfg, login, now):
        _log.info("cooldown: skipping comment by %s", login)
        return
    question = (
        "A community member mentioned the agent in a GitHub thread.\n\n"
        f"Issue: {issue.get('title', '')}\n\nComment: {body[:2000]}"
    )
    try:
        answer = _run_agent(graph, question)
    except GhError as exc:
        _log.error(
            "no answer for comment on %s#%s: %s", slug, issue.get("number"), exc
        )
        return
    gh.post_comment(slug, issue["number"], answer.text.strip() + FOOTER)
    _log.info("answered comment on %s#%s", slug, issue.get("number"))
