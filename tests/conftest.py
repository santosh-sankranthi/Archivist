"""Shared test setup: ``src/`` importability + FalkorDB-backed fixtures.

The integration fixtures live here (not in one test module) so every
integration test can share one throwaway graph and one skip rule.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from archivist.graph import GraphWriter

_LLM_ENV_VARS = (
    "ARCHIVIST_LLM_BASE_URL",
    "ARCHIVIST_LLM_API_KEY",
    "ARCHIVIST_LLM_MODEL",
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
)


@pytest.fixture(autouse=True)
def _isolate_llm_env(monkeypatch):
    """Keep tests hermetic against ambient LLM config.

    ``scripts/ask.py`` loads a repo-root ``.env`` at import (by design) and a
    developer shell may export keys too; neither may change what the tests
    exercise, so every test starts with these variables cleared. Tests that
    need env config set their own (see ``test_llm``).
    """
    for name in _LLM_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def git_commit(
    repo: Path, *args: str, name: str = "Dev One", email: str = "dev@example.com"
) -> None:
    """Run one git command with a deterministic author identity."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
    }
    subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


CHAIN_UTIL = """\
def leaf():
    return 2


def helper():
    return leaf()
"""

CHAIN_APP = """\
from util import helper as h


def main():
    return h()


def unused():
    pass
"""


@pytest.fixture()
def chain_repo(tmp_path: Path) -> Path:
    """Tiny repo with call chain main -> helper -> leaf and two app.py authors."""
    repo = tmp_path / "chain"
    repo.mkdir()
    git_commit(repo, "init", "-q", "-b", "main")
    (repo / "util.py").write_text(CHAIN_UTIL)
    (repo / "app.py").write_text(CHAIN_APP)
    git_commit(repo, "add", ".")
    git_commit(repo, "commit", "-q", "-m", "add app and util")
    # Dev Two makes the next two app.py changes -> most recent owner.
    for body, message in (
        ("\n\ndef extra():\n    return 3\n", "extend app with extra"),
        ("\n\ndef extra():\n    return 4\n", "bump extra"),
    ):
        (repo / "app.py").write_text(CHAIN_APP + body)
        git_commit(repo, "add", ".", name="Dev Two", email="two@example.com")
        git_commit(
            repo,
            "commit",
            "-q",
            "-m",
            message,
            name="Dev Two",
            email="two@example.com",
        )
    return repo


@pytest.fixture(scope="session")
def falkordb_or_skip():
    """(host, port) of a reachable FalkorDB; skips integration tests if absent."""
    try:
        GraphWriter(host="127.0.0.1", port=6379, graph="test:probe").graph.query(
            "RETURN 1"
        )
    except Exception as exc:  # noqa: BLE001 - any connect error means skip
        pytest.skip(f"FalkorDB not reachable: {exc}")
    return ("127.0.0.1", 6379)


@pytest.fixture()
def temp_graph(falkordb_or_skip):
    """A scratch graph, wiped before and after each test.

    Per-process name: two concurrent suite runs (parallel review passes,
    CI shards) must not clobber each other's state — a shared name caused
    transient false reds during REVIEW-M3.
    """
    host, port = falkordb_or_skip
    name = f"test:archivist:{os.getpid()}"
    writer = GraphWriter(host=host, port=port, graph=name)
    writer.delete()
    yield name
    writer.delete()
