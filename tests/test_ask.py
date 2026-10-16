"""ask.py CLI tests (M3c review): every run records an ``agent-session``
episode; memory-write failures warn without eating the answer.

Drives the real ``scripts/ask.py`` ``main()`` with a scripted model — the
same code path a live run takes, without any network. FalkorDB-backed;
skips if absent.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from archivist.ingest import ingest
from archivist.llm import LLMError, LLMResponse
from archivist.tools import RepoGraph

ROOT = Path(__file__).resolve().parents[1]


def load_ask_module():
    """Import scripts/ask.py as a module (it lives outside the package)."""
    spec = importlib.util.spec_from_file_location(
        "ask_cli", ROOT / "scripts" / "ask.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ScriptedLLM:
    """Minimal ChatLLM stand-in: one canned response per call, no network."""

    def __init__(self, script):
        self.script = list(script)
        self.model = "stub-model"
        self.usage: dict = {}

    def is_configured(self) -> bool:
        return True

    def chat(self, messages, tools=None, tool_choice="auto"):
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def run_ask(monkeypatch, module, argv, script):
    monkeypatch.setattr(module, "ChatLLM", lambda: ScriptedLLM(script))
    monkeypatch.setattr(sys, "argv", ["ask.py", *argv])
    return module.main()


def sessions(rg):
    return rg.query(
        "MATCH (e:Episode {source: 'ask.py'})"
        " RETURN e.question AS question, e.answer AS answer, e.error AS error,"
        " e.partial AS partial ORDER BY e.id"
    )["results"]


def test_ask_records_session_and_no_memory_opts_out(
    chain_repo, falkordb_or_skip, temp_graph, monkeypatch, capsys
):
    host, port = falkordb_or_skip
    ingest(
        chain_repo, temp_graph, host=host, port=port, rebuild=True, log=lambda m: None
    )
    rg = RepoGraph(graph=temp_graph, host=host, port=port)
    module = load_ask_module()
    base = [
        "who calls helper?",
        "--graph",
        temp_graph,
        "--host",
        host,
        "--port",
        str(port),
    ]

    rc = run_ask(monkeypatch, module, base, [LLMResponse(content="stub answer")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "memory: session" in out and "recorded" in out
    rows = sessions(rg)
    assert len(rows) == 1
    assert rows[0]["question"] == "who calls helper?"
    assert rows[0]["answer"] == "stub answer" and rows[0]["error"] is None

    rc = run_ask(
        monkeypatch, module, [*base, "--no-memory"], [LLMResponse(content="again")]
    )
    assert rc == 0
    assert len(sessions(rg)) == 1  # nothing new recorded


def test_ask_records_failed_run_with_error(
    chain_repo, falkordb_or_skip, temp_graph, monkeypatch
):
    host, port = falkordb_or_skip
    ingest(
        chain_repo, temp_graph, host=host, port=port, rebuild=True, log=lambda m: None
    )
    rg = RepoGraph(graph=temp_graph, host=host, port=port)
    module = load_ask_module()

    rc = run_ask(
        monkeypatch,
        module,
        ["q", "--graph", temp_graph, "--host", host, "--port", str(port)],
        [LLMError("boom")],
    )
    assert rc == 1
    rows = sessions(rg)
    assert len(rows) == 1
    assert rows[0]["error"] == "boom" and rows[0]["partial"] is True


def test_ask_survives_memory_write_failure(
    chain_repo, falkordb_or_skip, temp_graph, monkeypatch, capsys
):
    host, port = falkordb_or_skip
    ingest(
        chain_repo, temp_graph, host=host, port=port, rebuild=True, log=lambda m: None
    )
    module = load_ask_module()

    class FailWrites(module.Memory):
        def record_episode(self, **kwargs):
            raise RuntimeError("simulated db drop")

    monkeypatch.setattr(module, "Memory", FailWrites)
    rc = run_ask(
        monkeypatch,
        module,
        ["q", "--graph", temp_graph, "--host", host, "--port", str(port)],
        [LLMResponse(content="stub answer")],
    )

    captured = capsys.readouterr()
    assert rc == 0  # the answer survives
    assert "stub answer" in captured.out
    assert "warning: memory write failed" in captured.err
    rg = RepoGraph(graph=temp_graph, host=host, port=port)
    assert rg.query("MATCH (e:Episode) RETURN count(*) AS n")["results"][0]["n"] == 0
