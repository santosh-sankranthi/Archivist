"""Ingest tests: parser, tree walker, git reader, and an end-to-end round trip.

The integration round uses a throwaway FalkorDB graph and is skipped when
FalkorDB is not reachable, so unit tests stay runnable anywhere.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from archivist import pyparse
from archivist.files import iter_files, lang_for
from archivist.gitinfo import collect_history
from archivist.graph import GraphWriter
from archivist.ingest import ingest

SAMPLE_SOURCE = """\
import os
import os.path as osp
from pathlib import Path
from . import views
from .models import Widget as W


def top(a, b=1):
    def inner():
        return a

    return inner


async def atop():
    pass


class Widget:
    def render(self):
        pass

    async def build(cls):
        pass


if True:
    def guarded():
        pass
"""


def test_parse_python_symbols_and_imports():
    parsed = pyparse.parse_python(SAMPLE_SOURCE)
    got = {(symbol.qualname, symbol.kind) for symbol in parsed.symbols}
    assert ("top", "function") in got
    assert ("top.inner", "function") in got
    assert ("atop", "function") in got
    assert ("Widget", "class") in got
    assert ("Widget.render", "method") in got
    assert ("Widget.build", "method") in got
    assert ("guarded", "function") in got
    modules = {ref.module for ref in parsed.imports}
    assert {"os", "os.path", "pathlib", ".", ".models"} <= modules


def test_parse_python_raises_on_bad_source():
    with pytest.raises(SyntaxError):
        pyparse.parse_python("def broken(")


def test_iter_files_skips_noise_and_binary(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("x = 1\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "readme.md").write_text("# hi\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]\n")
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02")
    infos = iter_files(tmp_path)
    assert [info.path for info in infos] == ["docs/readme.md", "pkg/mod.py"]  # sorted
    assert {info.lang for info in infos} == {"markdown", "python"}
    assert lang_for(Path("x.json")) == "json"
    assert lang_for(Path("x.unknown")) == "other"


def _git(repo: Path, *args: str) -> None:
    # Force a fixed author/committer identity: host-level GIT_* env vars must
    # not leak into fixture repos, or history tests become environment-dependent.
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Dev One",
        "GIT_AUTHOR_EMAIL": "dev@example.com",
        "GIT_COMMITTER_NAME": "Dev One",
        "GIT_COMMITTER_EMAIL": "dev@example.com",
    }
    subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """Tiny repo with two commits and one file modification."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Dev One")
    _git(repo, "config", "user.email", "dev@example.com")
    (repo / "app.py").write_text("def a():\n    return 1\n")
    (repo / "README.md").write_text("# demo\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "add app module")
    (repo / "app.py").write_text("def a():\n    return 1\n\n\ndef b():\n    return 2\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "extend app module\n\nsecond line body")
    return repo


def test_collect_history(git_repo: Path):
    commits = collect_history(git_repo)
    assert len(commits) == 2
    newest, first = commits
    assert newest.parents == [first.sha]
    assert newest.summary == "extend app module"
    assert "second line body" in newest.message
    assert newest.author_email == "dev@example.com"
    assert {change.path for change in newest.changes} == {"app.py"}
    assert (newest.changes[0].added, newest.changes[0].removed) == (4, 0)


@pytest.fixture(scope="session")
def falkordb_or_skip():
    try:
        GraphWriter(host="127.0.0.1", port=6379, graph="test:probe").graph.query(
            "RETURN 1"
        )
    except Exception as exc:  # noqa: BLE001 - any connect error means skip
        pytest.skip(f"FalkorDB not reachable: {exc}")
    return ("127.0.0.1", 6379)


@pytest.fixture()
def temp_graph(falkordb_or_skip):
    host, port = falkordb_or_skip
    name = "test:archivist"
    writer = GraphWriter(host=host, port=port, graph=name)
    writer.delete()
    yield name
    writer.delete()


def test_ingest_end_to_end(git_repo: Path, falkordb_or_skip, temp_graph: str):
    host, port = falkordb_or_skip
    stats = ingest(git_repo, temp_graph, host=host, port=port, rebuild=True)

    assert stats.counts["nodes"] == {
        "Author": 1,
        "Commit": 2,
        "File": 2,
        "Repo": 1,
        "Symbol": 2,
    }
    assert stats.counts["edges"]["AUTHORED"] == 2
    assert stats.counts["edges"]["MODIFIED"] == 3  # commit1: app+readme, commit2: app
    assert stats.counts["edges"]["PARENT"] == 1
    assert stats.counts["edges"]["DEFINES"] == 2
    assert stats.counts["edges"]["HEAD"] == 1

    # Idempotent re-ingest: identical counts.
    stats2 = ingest(git_repo, temp_graph, host=host, port=port)
    assert stats2.counts == stats.counts

    # Evolve the repo: drop `def b` — prune must remove the stale Symbol node.
    (git_repo / "app.py").write_text("def a():\n    return 1\n")
    _git(git_repo, "add", ".")
    _git(git_repo, "commit", "-q", "-m", "drop b")
    stats3 = ingest(git_repo, temp_graph, host=host, port=port)
    assert stats3.counts["nodes"]["Commit"] == 3
    assert stats3.counts["nodes"]["Symbol"] == 1
    assert stats3.counts["edges"]["DEFINES"] == 1
