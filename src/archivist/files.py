"""Repository tree walking for ingest v0.

Determinism matters: the same tree must yield the same ingest input, so we
sort paths, skip VCS/cache noise directories, skip symlinks, and skip binary
files (a NUL byte in the first blocks is the classic heuristic).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Directories that never carry project source we want in the graph.
SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".tox",
    ".nox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "site-packages",
    ".idea",
    ".vscode",
}

_EXT_LANG = {
    ".py": "python",
    ".pyi": "python",
    ".md": "markdown",
    ".rst": "rst",
    ".txt": "text",
    ".toml": "toml",
    ".cfg": "ini",
    ".ini": "ini",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".json": "json",
    ".html": "html",
    ".css": "css",
    ".js": "javascript",
    ".ts": "typescript",
    ".sh": "shell",
}


@dataclass(frozen=True)
class FileInfo:
    """A text file in the repo: repo-relative posix path, language, line count."""

    path: str
    lang: str
    loc: int


def lang_for(path: Path) -> str:
    """Map a file extension to a coarse language label."""
    return _EXT_LANG.get(path.suffix.lower(), "other")


def _is_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def iter_files(root: Path) -> list[FileInfo]:
    """Return FileInfo for every text file under ``root``, sorted by path."""
    infos: list[FileInfo] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts[:-1]):
            continue
        data = path.read_bytes()
        if _is_binary(data):
            continue
        infos.append(
            FileInfo(
                path=path.relative_to(root).as_posix(),
                lang=lang_for(path),
                loc=len(data.splitlines()),
            )
        )
    return infos
