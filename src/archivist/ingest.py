"""Ingest orchestration: repository tree + git history -> FalkorDB graph.

Scope: files, Python symbols/imports/calls, commits, authors, and
commit->file modification stats. Call resolution (M1c) is best-effort,
name-based — see ``archivist.calls`` for the rules and
``docs/graph-model.md`` §CALLS for the known gaps.

All writes are idempotent MERGEs plus a run-stamp prune, so `ingest()` can be
re-run at any time: the graph converges to the repo's current state.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from archivist.calls import resolve_calls
from archivist.files import iter_files
from archivist.gitinfo import collect_history, head_sha, is_shallow
from archivist.graph import GraphWriter
from archivist.pyparse import ParsedModule, parse_python


@dataclass
class IngestStats:
    """Everything a caller (CLI, tests, later the agent) needs to know."""

    graph: str
    repo: str
    head: str
    run_id: str
    duration_s: float
    files: int
    parse_failures: int
    shallow: bool
    counts: dict = field(default_factory=dict)
    call_stats: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def utc_run_id() -> str:
    """Unique id per ingest run.

    The stamp is what prune compares, so uniqueness matters even for
    back-to-back runs: a UTC second alone can collide, hence the suffix.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid4().hex[:8]}"


def ingest(
    repo: Path | str,
    graph_name: str,
    *,
    host: str = "127.0.0.1",
    port: int = 6379,
    run_id: str | None = None,
    prune: bool = True,
    rebuild: bool = False,
    log: Callable[[str], None] = print,
) -> IngestStats:
    """Ingest ``repo`` into ``graph_name`` and return summary statistics."""
    started = time.monotonic()
    run_id = run_id or utc_run_id()
    repo = Path(repo).resolve()
    if not (repo / ".git").exists():
        raise ValueError(f"not a git repository: {repo}")
    log(f"ingest: repo={repo}")
    log(f"ingest: graph={graph_name} falkordb={host}:{port} run_id={run_id}")

    log("[1/6] walking tree")
    file_infos = iter_files(repo)
    log(f"      {len(file_infos)} text files")

    file_rows: list[dict] = []
    symbol_rows: list[dict] = []
    import_rows: list[dict] = []
    parsed_by_file: dict[str, ParsedModule] = {}
    parse_failures = 0
    for info in file_infos:
        file_rows.append({"path": info.path, "lang": info.lang, "loc": info.loc})
        if info.lang != "python":
            continue
        source = (repo / info.path).read_text(encoding="utf-8", errors="replace")
        try:
            parsed = parse_python(source)
        except SyntaxError:
            parse_failures += 1  # fixture files etc.; keep the file node anyway
            continue
        parsed_by_file[info.path] = parsed
        for sym in parsed.symbols:
            symbol_rows.append(
                {
                    "path": info.path,
                    "name": sym.name,
                    "kind": sym.kind,
                    "qualname": sym.qualname,
                    "lineno": sym.lineno,
                }
            )
        for imp in parsed.imports:
            import_rows.append(
                {"path": info.path, "module": imp.module, "line": imp.line}
            )
    call_sites = sum(len(module.calls) for module in parsed_by_file.values())
    log(
        f"      symbols={len(symbol_rows)} imports={len(import_rows)} "
        f"calls={call_sites} parse_failures={parse_failures}"
    )

    log("[2/6] resolving call sites")
    python_files = [info.path for info in file_infos if info.lang == "python"]
    call_rows, call_stats = resolve_calls(parsed_by_file, python_files)
    log(
        f"      edges={len(call_rows)} exact={call_stats['exact']} "
        f"weak={call_stats['weak']} unresolved={call_stats['unresolved']} "
        f"(ambiguous={call_stats['ambiguous']})"
    )

    log("[3/6] reading git history")
    commits = collect_history(repo)
    head = head_sha(repo)
    shallow = is_shallow(repo)
    if shallow:
        log("      WARNING: shallow clone — history truncated, PARENT stubs possible")

    commit_rows: list[dict] = []
    parent_rows: list[dict] = []
    modification_rows: list[dict] = []
    author_commits: Counter[str] = Counter()
    author_names: dict[str, str] = {}
    for commit in commits:
        commit_rows.append(
            {
                "sha": commit.sha,
                "author_name": commit.author_name,
                "author_email": commit.author_email,
                "ts": commit.ts,
                "summary": commit.summary,
                "message": commit.message,
            }
        )
        parent_rows.extend({"sha": commit.sha, "parent": p} for p in commit.parents)
        modification_rows.extend(
            {
                "sha": commit.sha,
                "path": change.path,
                "added": change.added,
                "removed": change.removed,
            }
            for change in commit.changes
        )
        author_commits[commit.author_email] += 1
        author_names.setdefault(commit.author_email, commit.author_name)
    author_rows = [
        {"email": email, "name": author_names[email], "commits_count": count}
        for email, count in author_commits.items()
    ]
    log(
        f"      {len(commits)} commits · {len(author_rows)} authors · {len(modification_rows)} file changes"
    )

    log("[4/6] writing graph")
    writer = GraphWriter(host=host, port=port, graph=graph_name)
    if rebuild:
        writer.delete()
    writer.write_commits(commit_rows, run_id)
    writer.write_parents(parent_rows)
    writer.write_authors(author_rows)
    writer.write_files(file_rows, run_id)
    writer.write_symbols(symbol_rows, run_id)
    writer.write_imports(import_rows, run_id)
    writer.write_calls(call_rows, run_id)
    writer.write_modifications(modification_rows, run_id)
    writer.write_repo(
        name=repo.name,
        path=str(repo),
        head=head,
        updated_at=int(time.time()),
        run_id=run_id,
    )

    if prune:
        log("[5/6] pruning stale nodes")
        writer.prune(run_id)
    else:
        log("[5/6] prune skipped")

    counts = writer.stats()
    duration = time.monotonic() - started
    log(f"[6/6] done in {duration:.1f}s")
    return IngestStats(
        graph=graph_name,
        repo=str(repo),
        head=head,
        run_id=run_id,
        duration_s=round(duration, 2),
        files=len(file_rows),
        parse_failures=parse_failures,
        shallow=shallow,
        counts=counts,
        call_stats=call_stats,
    )


# Canned Cypher used as smoke tests after ingest (and reused by tests/docs).
# Kept as plain strings so demo transcripts and the future agent can share them.
SMOKE_QUERIES: dict[str, str] = {
    "top authors by commits": """
        MATCH (a:Author)-[:AUTHORED]->(c:Commit)
        RETURN a.name AS author, count(c) AS commits
        ORDER BY count(c) DESC LIMIT 5
    """,
    "most-touched files (with churn)": """
        MATCH (c:Commit)-[m:MODIFIED]->(f:File)
        RETURN f.path AS path, count(c) AS touches,
               sum(coalesce(m.added, 0) + coalesce(m.removed, 0)) AS churn
        ORDER BY count(c) DESC LIMIT 5
    """,
    "symbol hotspots (files by defs)": """
        MATCH (f:File)-[:DEFINES]->(s:Symbol)
        RETURN f.path AS path, count(s) AS symbols
        ORDER BY count(s) DESC LIMIT 5
    """,
    "3-hop: who touches importers of 'flask'": """
        MATCH (m:Module {name: 'flask'})<-[:IMPORTS]-(f:File)<-[:MODIFIED]-(c:Commit)<-[:AUTHORED]-(a:Author)
        RETURN a.name AS author, count(DISTINCT f) AS files
        ORDER BY count(DISTINCT f) DESC LIMIT 5
    """,
    "most-called symbols (fan-in)": """
        MATCH (caller:Symbol)-[c:CALLS]->(callee:Symbol)
        RETURN callee.qualname AS callee, callee.file AS file,
               sum(c.count) AS calls, count(DISTINCT caller) AS callers
        ORDER BY sum(c.count) DESC LIMIT 10
    """,
    "cross-file calls (top pairs)": """
        MATCH (a:Symbol)-[c:CALLS]->(b:Symbol)
        WHERE a.file <> b.file
        RETURN a.qualname AS caller, b.qualname AS callee,
               sum(c.count) AS calls
        ORDER BY sum(c.count) DESC LIMIT 10
    """,
}


def run_smoke(writer: GraphWriter) -> dict[str, list[list]]:
    """Run the canned smoke queries against the ingested graph."""
    return {name: writer.smoke(query) for name, query in SMOKE_QUERIES.items()}
