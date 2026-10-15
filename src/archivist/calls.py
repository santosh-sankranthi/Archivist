"""Repo-wide call-site resolution (M1c) — best-effort, honest about it.

Why name-based resolution instead of a real type checker: v0 needs *useful
and truthful* CALLS edges for impact analysis, and full Python name binding
is a research project. We resolve in widening rings, stop at the first
confident match, and count everything we skip — no cross-scope guessing:

1. Same file, scope-aware: try ``<caller-scope-prefix>.<name>`` from the
   innermost scope outward, so nested defs, sibling methods (``self.m``) and
   top-level defs resolve exactly.
2. Same file, unique name: exactly one symbol carries that name (weak match,
   counted as such — it ignores scope, e.g. a lone ``helper`` defined inside
   another function).
3. Cross-file via import bindings: resolve the dotted target of ``import`` /
   ``from ... import`` against a module index built from file paths (longest
   module prefix wins; symbol looked up in the target file, chasing package
   ``__init__.py`` re-exports up to two hops). Relative imports are anchored
   to the caller file's directory.

Everything else (stdlib, third-party, dynamic calls) stays unresolved and is
counted; ingest logs the coverage so the honest number is always visible.
See docs/graph-model.md §CALLS for the semantics and the known gaps.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath

from archivist.pyparse import ImportBinding, ParsedModule, Symbol

__all__ = ["CallEdge", "resolve_calls"]

# ``self``/``cls`` are resolved against the caller's enclosing scope by
# convention, never against imports.
_SELF_NAMES = frozenset({"self", "cls"})


@dataclass(frozen=True)
class CallEdge:
    """One aggregated caller -> callee relation (all call sites collapsed)."""

    caller_file: str
    caller_qual: str
    callee_file: str
    callee_qual: str
    count: int
    lines: tuple[int, ...]


def _module_keys(path: str) -> list[str]:
    """Candidate dotted module names for a ``.py`` path, longest suffix first.

    ``src/flask/app.py`` -> ``src.flask.app``, ``flask.app``, ``app``.
    Keeping every suffix absorbs the "where is the package root?" question
    (src layout vs flat vs tests): the lookup side prefers the longest match,
    and files that would collide on the short keys just never get one.
    ``__init__.py`` keys drop the stem (the package itself). Stubs (``.pyi``)
    are not importable units — skipped.
    """
    if path.endswith(".pyi"):
        return []
    parts = path[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return []
    return [".".join(parts[i:]) for i in range(len(parts))]


class _Repo:
    """Symbol and module indexes built once per ingest run."""

    def __init__(
        self, parsed_by_file: dict[str, ParsedModule], python_files: list[str]
    ) -> None:
        self.symbols: dict[str, dict[str, Symbol]] = {
            path: {sym.qualname: sym for sym in module.symbols}
            for path, module in parsed_by_file.items()
        }
        self.bindings: dict[str, dict[str, ImportBinding]] = {
            path: {binding.name: binding for binding in module.bindings}
            for path, module in parsed_by_file.items()
        }
        self.by_name: dict[str, dict[str, list[str]]] = {}
        for path, table in self.symbols.items():
            names: dict[str, list[str]] = defaultdict(list)
            for qualname, sym in table.items():
                names[sym.name].append(qualname)
            self.by_name[path] = {name: sorted(q) for name, q in names.items()}
        self.modules: dict[str, list[str]] = defaultdict(list)
        for path in python_files:
            for key in _module_keys(path):
                self.modules[key].append(path)
        for files in self.modules.values():
            # Deterministic pick: fewest path parts, then lexicographic.
            files.sort(key=lambda p: (len(p.split("/")), p))

    def file_for_module(self, key: str) -> str | None:
        files = self.modules.get(key)
        return files[0] if files else None

    def symbol_in(self, file: str, path: str, depth: int = 0) -> tuple[str, str] | None:
        """Resolve dotted ``path`` inside ``file``.

        Exact qualname first; then follow the file's own import bindings
        (package ``__init__.py`` re-exports like ``from .app import Flask``)
        up to two hops; then, for single names, the unique-name fallback.
        """
        table = self.symbols.get(file)
        if table is None:
            return None
        if path in table:
            return file, path
        parts = path.split(".")
        if depth < 2:
            binding = self.bindings.get(file, {}).get(parts[0])
            if binding:
                target = binding.target
                if len(parts) > 1:
                    target = f"{target}.{'.'.join(parts[1:])}"
                hit = _resolve_symbol_path(self, file, target, depth + 1)
                if hit:
                    return hit
        if len(parts) == 1:
            candidates = self.by_name.get(file, {}).get(parts[0], [])
            if len(candidates) == 1:
                return file, candidates[0]
        return None


def _resolve_symbol_path(
    repo: _Repo, caller_file: str, target: str, depth: int = 0
) -> tuple[str, str] | None:
    """Resolve a dotted ``target`` (absolute or relative) to (file, qualname).

    Longest module prefix first (``flask.helpers.url_for`` tries module
    ``flask.helpers`` + symbol ``url_for`` before anything shorter). Relative
    targets (leading dots) anchor at the caller's directory: one dot = its
    package/containing dir, each extra dot climbs one level.
    """
    level = len(target) - len(target.lstrip("."))
    rest = [part for part in target.lstrip(".").split(".") if part]
    if level:
        base = PurePosixPath(caller_file).parent
        base_parts = [] if str(base) == "." else list(base.parts)
        if level > 1:
            base_parts = base_parts[: max(len(base_parts) - (level - 1), 0)]
        parts = base_parts + rest
    else:
        parts = rest
    for size in range(len(parts), 0, -1):
        module_key = ".".join(parts[:size])
        file = repo.file_for_module(module_key)
        if file is None:
            continue
        symbol_path = ".".join(parts[size:])
        if not symbol_path:
            continue  # bare module — not a symbol edge
        hit = repo.symbol_in(file, symbol_path, depth)
        if hit:
            return hit
    return None


def _scoped_lookup(
    repo: _Repo, file: str, caller: str, rest: list[str]
) -> tuple[str, str] | None:
    """Try ``<caller-scope-prefix> + rest`` from the innermost scope outward.

        ``helper()`` inside ``outer`` finds ``outer.helper``; a nested ``inner``
        call inside ``top`` finds ``top.inner``; ``depth 0`` is the file's
    top level (plain global lookup).
    """
    table = repo.symbols.get(file)
    if not table:
        return None
    scope = caller.split(".")
    for depth in range(len(scope), -1, -1):
        candidate = ".".join(scope[:depth] + rest)
        if candidate in table:
            return file, candidate
    return None


def _self_lookup(
    repo: _Repo, file: str, caller: str, rest: list[str]
) -> tuple[str, str] | None:
    """Resolve ``self.m`` / ``cls.m`` against the *enclosing class* only.

    A method can reach its siblings and defs nested inside itself, but
    ``self.step`` must never leak to a same-named top-level def — the class
    scope is the floor. Falls back to the plain scope walk when no enclosing
    class symbol is found (self used outside a class body).
    """
    table = repo.symbols.get(file)
    if not table:
        return None
    scope = caller.split(".")
    class_depth: int | None = None
    for depth in range(len(scope), 0, -1):
        sym = table.get(".".join(scope[:depth]))
        if sym is not None and sym.kind == "class":
            class_depth = depth
            break
    if class_depth is None:
        return _scoped_lookup(repo, file, caller, rest)
    for depth in range(len(scope), class_depth - 1, -1):
        candidate = ".".join(scope[:depth] + rest)
        if candidate in table:
            return file, candidate
    return None


def _resolve_call(
    repo: _Repo,
    caller_file: str,
    callee: str,
    caller: str,
    bindings: dict[str, ImportBinding],
    stats: dict[str, int],
) -> tuple[str, str] | None:
    parts = callee.split(".")
    if len(parts) > 1 and parts[0] in _SELF_NAMES:
        hit = _self_lookup(repo, caller_file, caller, parts[1:])
        if hit:
            stats["exact"] += 1
        return hit

    if len(parts) == 1:
        hit = _scoped_lookup(repo, caller_file, caller, parts)
        if hit:
            stats["exact"] += 1
            return hit
        candidates = repo.by_name.get(caller_file, {}).get(parts[0])
        if candidates:
            if len(candidates) == 1:
                stats["weak"] += 1
                return caller_file, candidates[0]
            stats["ambiguous"] += 1
            return None
        binding = bindings.get(parts[0])
        if binding:
            hit = _resolve_symbol_path(repo, caller_file, binding.target)
            if hit:
                stats["exact"] += 1
            return hit
        return None

    # Dotted callee, not self/cls.
    binding = bindings.get(parts[0])
    if binding:
        target = f"{binding.target}.{'.'.join(parts[1:])}"
        hit = _resolve_symbol_path(repo, caller_file, target)
        if hit:
            stats["exact"] += 1
            return hit

    # A locally defined class used as an attribute base: ``Widget.make()``.
    hit = _scoped_lookup(repo, caller_file, caller, parts)
    if hit:
        stats["exact"] += 1
        return hit

    # Absolute module path with no binding recorded (e.g. re-exports).
    hit = _resolve_symbol_path(repo, caller_file, callee)
    if hit:
        stats["weak"] += 1
    return hit


def resolve_calls(
    parsed_by_file: dict[str, ParsedModule], python_files: list[str]
) -> tuple[list[dict], dict[str, int]]:
    """Resolve every recorded call site into aggregated CALLS rows + stats.

    Returns ``(rows, stats)`` where rows carry ``caller_qual/callee_qual``
    (plus files, count, sorted lines) ready for the graph writer, and stats
    report coverage: ``call_sites``, ``exact``, ``weak``, ``unresolved``,
    ``ambiguous`` (subset of unresolved caused by >1 same-file name match).
    Deterministic: iterate files sorted, aggregate per caller->callee pair,
    sort rows by key.
    """
    repo = _Repo(parsed_by_file, sorted(python_files))
    aggregated: dict[tuple[str, str, str, str], list[int]] = defaultdict(list)
    stats = {"call_sites": 0, "exact": 0, "weak": 0, "unresolved": 0, "ambiguous": 0}

    for path in sorted(parsed_by_file):
        module = parsed_by_file[path]
        bindings = {binding.name: binding for binding in module.bindings}
        for call in module.calls:
            stats["call_sites"] += 1
            hit = _resolve_call(repo, path, call.callee, call.caller, bindings, stats)
            if hit is None:
                stats["unresolved"] += 1
                continue
            key = (path, call.caller, hit[0], hit[1])
            aggregated[key].append(call.line)

    rows = [
        {
            "caller_file": key[0],
            "caller_qual": key[1],
            "callee_file": key[2],
            "callee_qual": key[3],
            "count": len(lines),
            "lines": sorted(lines),
        }
        for key, lines in sorted(aggregated.items())
    ]
    return rows, stats
