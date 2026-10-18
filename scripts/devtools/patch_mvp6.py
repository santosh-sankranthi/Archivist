#!/usr/bin/env python3
"""Patch 6: word-boundary security hints + model-rotating retries.

Run from the repo root:
    .venv/bin/python scripts/devtools/patch_mvp6.py
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HANDLERS = ROOT / "src/archivist/gh/handlers.py"

PATCHES: list[tuple[str, str, str]] = [
    (
        "p6a-import-os",
        '''import logging
import re
import time
''',
        '''import logging
import os
import re
import time
''',
    ),
    (
        "p6b-security-regex",
        '''_SECURITY_HINTS = (
    "vulnerability",
    "security issue",
    "cve-",
    "exploit",
    "sql injection",
    "rce",
    "remote code execution",
    "privilege escalation",
)
''',
        '''_SECURITY_RE = re.compile(
    r"\\b(vulnerability|security issue|cve-|exploit|sql injection|rce|"
    r"remote code execution|privilege escalation)\\b"
)
''',
    ),
    (
        "p6c-security-usage",
        '''    text = f"{title}\\n\\n{body}"
    if any(hint in text.lower() for hint in _SECURITY_HINTS):
''',
        '''    text = f"{title}\\n\\n{body}"
    if _SECURITY_RE.search(text.lower()):
''',
    ),
    (
        "p6d-model-rotation",
        '''def _run_agent(graph: str, question: str, *, attempts: int = 2):
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
''',
        '''def _model_chain() -> list[str | None]:
    """Attempt models: env default, the free-tier fallback, env default again."""
    fallback = (
        os.environ.get("ARCHIVIST_GH_MODEL_FALLBACK") or "longcat-2.5-preview-free"
    ).strip()
    return [None, fallback or None, None]


def _run_agent(graph: str, question: str, *, attempts: int = 3):
    """One grounded answer from the community graph.

    Empty finalizations ("(no answer produced)") are a known flake of some
    free models, so empty runs retry, rotating through the model chain.
    """
    chain = _model_chain()
    last: str = "unknown"
    for attempt in range(attempts):
        model = chain[attempt] if attempt < len(chain) else None
        llm = ChatLLM(model=model)
        if not llm.is_configured():
            raise GhError("LLM not configured on the server")
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
            "empty answer (attempt %s, model=%s): %s",
            attempt + 1,
            llm.model,
            last[:120],
        )
    raise GhError(f"agent produced no answer ({last})")
''',
    ),
]


def main() -> int:
    text = HANDLERS.read_text(encoding="utf-8")
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
        HANDLERS.write_text(text, encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
