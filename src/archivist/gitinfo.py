"""Git history extraction for ingest v0.

Design: two single-pass ``git log`` calls (structure + numstat first, full
messages second) instead of per-commit plumbing — ~5k commits parse in a
second or two, and the output is stable across machines. Merge-commit diffs
are skipped by git default (no numstat rows); renames arrive as delete+add
pairs because we pass ``--no-renames``. See docs/graph-model.md for how this
shapes the graph.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_REC = "\x1e"  # record separator between commits
_FLD = "\x1f"  # field separator inside the header line
_NUMSTAT_RE = re.compile(r"^(?P<added>\d+|-)\t(?P<removed>\d+|-)\t(?P<path>.+)$")


@dataclass(frozen=True)
class FileChange:
    """One file entry of a commit's diff. added/removed are None for binary."""

    path: str
    added: int | None
    removed: int | None


@dataclass
class GitCommit:
    sha: str
    author_name: str
    author_email: str
    ts: int  # UTC epoch seconds
    parents: list[str]
    summary: str
    message: str = ""
    changes: list[FileChange] = field(default_factory=list)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "-c", "core.quotepath=false", *args],
        capture_output=True,
        check=True,
    )
    return result.stdout.decode("utf-8", errors="replace")


def head_sha(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").strip()


def is_shallow(repo: Path) -> bool:
    return _git(repo, "rev-parse", "--is-shallow-repository").strip() == "true"


def collect_history(repo: Path) -> list[GitCommit]:
    """Parse full ``git log`` output (newest commit first) into records."""
    raw = _git(
        repo,
        "log",
        "--numstat",
        "--no-renames",
        f"--pretty=format:{_REC}%H{_FLD}%an{_FLD}%ae{_FLD}%at{_FLD}%P{_FLD}%s",
    )
    commits: list[GitCommit] = []
    for chunk in raw.split(_REC):
        if not chunk.strip():
            continue
        lines = chunk.split("\n")
        parts = lines[0].split(_FLD, 5)
        if len(parts) != 6:
            continue
        sha, author_name, author_email, ts, parents, summary = parts
        commit = GitCommit(
            sha=sha,
            author_name=author_name,
            author_email=author_email,
            ts=int(ts),
            parents=parents.split(),
            summary=summary,
        )
        for line in lines[1:]:
            match = _NUMSTAT_RE.match(line)
            if match:
                commit.changes.append(
                    FileChange(
                        path=match.group("path"),
                        added=(
                            None
                            if match.group("added") == "-"
                            else int(match.group("added"))
                        ),
                        removed=(
                            None
                            if match.group("removed") == "-"
                            else int(match.group("removed"))
                        ),
                    )
                )
        commits.append(commit)

    bodies = _collect_messages(repo)
    for commit in commits:
        commit.message = bodies.get(commit.sha, "") or commit.summary
    return commits


def _collect_messages(repo: Path) -> dict[str, str]:
    """sha -> full commit message (subject + body), trimmed."""
    raw = _git(repo, "log", f"--pretty=format:{_REC}%H{_FLD}%B")
    bodies: dict[str, str] = {}
    for chunk in raw.split(_REC):
        if not chunk:
            continue
        sha, _, body = chunk.partition(_FLD)
        bodies[sha] = body.strip()
    return bodies
