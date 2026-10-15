"""Repo-wide call-site resolution (M1c/M1d) — best-effort, honest about it.

Why name-based resolution instead of a real type checker: v0 needs *useful
and truthful* CALLS edges for impact analysis, and full Python name binding
is a research project. We resolve in widening rings, stop at the first match
worth trusting, and count what we skip — no silent guessing:

1. Same file, function scopes only: ``<caller-scope-prefix>.<name>`` from the
   innermost scope outward, skipping class scopes (Python name resolution
   never enters them; ``self.m``/``cls.m`` is the only route into a class and
   is handled separately with the enclosing class as its floor).
2. Import bindings: ``import`` / ``from ... import`` targets, respecting the
   binding's scope (module-level or function-local) and chasing package
   ``__init__.py`` re-exports up to two hops. Relative targets anchor at the
   caller's directory; absolute targets only match module keys that could
   genuinely be importable (a key whose skipped directories contain no
   ``__init__.py`` — a packaged prefix cannot be a sys.path root, so e.g.
   `json.dumps` never resolves to ``flask/json/``'s file).
3. Same file, unique name — a *weak* match: exactly one non-class-scoped
   symbol carries the name (counted as ``weak_unique``; scope-blind).
4. Absolute module path with no binding — a *weak* guess: longest module key
   + symbol lookup (counted as ``weak_module``; right by naming luck, not
   semantics, e.g. ``app.test_client`` in test fixtures).

Everything else (stdlib, third-party, calls on local variables, ambiguous
names) stays unresolved and is counted, so the honest coverage is visible.
Edges resolved via rings 3–4 carry ``weak=True`` so queries can down-rank or
filter guesses. See docs/graph-model.md §CALLS for semantics and gaps.
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
    weak: bool


def _module_key_entries(path: str, dirs_with_init: set[str]) -> list[tuple[str, bool]]:
    """Candidate dotted module names for a ``.py`` path, longest suffix first.

    ``src/flask/app.py`` -> ``src.flask.app``, ``flask.app``, ``app``. Every
    suffix stays a candidate (repos disagree on where the package root is),
    but each key carries a ``packaged`` flag: True when the directories it
    would skip contain an ``__init__.py``, i.e. the key starts *inside* a
    package and could never be a top-level importable name. Strict lookups
    (import-statement targets) refuse packaged keys; lenient lookups (the
    weak ring-4 guess) keep them. ``__init__.py`` drops the stem; stubs
    (``.pyi``) are not importable units — skipped.
    """
    if path.endswith(".pyi"):
        return []
    parts = path[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return []
    entries: list[tuple[str, bool]] = []
    for i in range(len(parts)):
        key = ".".join(parts[i:])
        base_dirs = ["/".join(parts[:j]) for j in range(1, i + 1)]
        packaged = any(d in dirs_with_init for d in base_dirs)
        entries.append((key, packaged))
    return entries


class _Repo:
    """Symbol, binding and module indexes built once per ingest run."""

    def __init__(
        self, parsed_by_file: dict[str, ParsedModule], python_files: list[str]
    ) -> None:
        self.symbols: dict[str, dict[str, Symbol]] = {
            path: {sym.qualname: sym for sym in module.symbols}
            for path, module in parsed_by_file.items()
        }
        self.by_name: dict[str, dict[str, list[str]]] = {}
        for path, table in self.symbols.items():
            names: dict[str, list[str]] = defaultdict(list)
            for qualname, sym in table.items():
                names[sym.name].append(qualname)
            self.by_name[path] = {name: sorted(q) for name, q in names.items()}
        # All bindings (scoped) for call resolution; module-level only for
        # the re-export chase (only module level can re-export).
        self.bindings_by_name: dict[str, dict[str, list[ImportBinding]]] = {}
        self.reexports: dict[str, dict[str, ImportBinding]] = {}
        for path, module in parsed_by_file.items():
            by_name: dict[str, list[ImportBinding]] = defaultdict(list)
            for binding in module.bindings:
                by_name[binding.name].append(binding)
            self.bindings_by_name[path] = dict(by_name)
            self.reexports[path] = {
                b.name: b for b in module.bindings if b.scope is None
            }
        dirs_with_init = {
            PurePosixPath(p).parent.as_posix()
            for p in python_files
            if p.endswith("__init__.py")
        }
        entries: dict[str, list[tuple[str, bool]]] = defaultdict(list)
        for path in python_files:
            for key, packaged in _module_key_entries(path, dirs_with_init):
                entries[key].append((path, packaged))
        self.modules: dict[str, list[tuple[str, bool]]] = {}
        for key, candidates in entries.items():
            # Deterministic pick: fewest path parts, then lexicographic.
            candidates.sort(key=lambda item: (len(item[0].split("/")), item[0]))
            self.modules[key] = candidates

    def file_for_module(self, key: str, strict: bool = False) -> str | None:
        """File for a module key; ``strict`` refuses packaged-prefix keys."""
        for path, packaged in self.modules.get(key, []):
            if not strict or not packaged:
                return path
        return None

    def symbol_in(self, file: str, path: str, depth: int = 0) -> tuple[str, str] | None:
        """Resolve dotted ``path`` inside ``file``.

        Exact qualname first; then follow the file's module-level import
        bindings (package ``__init__.py`` re-exports like ``from .app import
        Flask``) up to two hops; then, for single names, the unique-name
        namespace lookup (any symbol kind — this is a module namespace, not a
        scope chain).
        """
        table = self.symbols.get(file)
        if table is None:
            return None
        if path in table:
            return file, path
        parts = path.split(".")
        if depth < 2:
            binding = self.reexports.get(file, {}).get(parts[0])
            if binding:
                target = binding.target
                if len(parts) > 1:
                    target = f"{target}.{'.'.join(parts[1:])}"
                hit = _resolve_symbol_path(self, file, target, depth + 1, strict=True)
                if hit:
                    return hit
        if len(parts) == 1:
            candidates = self.by_name.get(file, {}).get(parts[0], [])
            if len(candidates) == 1:
                return file, candidates[0]
        return None


def _resolve_symbol_path(
    repo: _Repo, caller_file: str, target: str, depth: int = 0, strict: bool = False
) -> tuple[str, str] | None:
    """Resolve a dotted ``target`` (absolute or relative) to (file, qualname).

    Longest module prefix first (``flask.helpers.url_for`` tries module
    ``flask.helpers`` + symbol ``url_for`` before anything shorter). Relative
    targets (leading dots) anchor at the caller's directory: one dot = its
    package/containing dir, each extra dot climbs one level. ``strict`` marks
    targets that came from import statements: those only match module keys
    that could genuinely be top-level importable names.
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
        file = repo.file_for_module(".".join(parts[:size]), strict=strict)
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

    Scope-accurate: class-level prefixes are skipped (Python name resolution
    never enters class scopes — a bare ``helper()`` inside a method must not
    find ``C.helper``), while nested defs and enclosing function scopes are
    tried exactly. Depth 0 is the file's top level. The one exception is the
    caller's own scope when it *is* a class body (class-body statements run
    with the class namespace in place, so their own defs are visible).
    """
    table = repo.symbols.get(file)
    if not table:
        return None
    scope = caller.split(".")
    for depth in range(len(scope), -1, -1):
        if 0 < depth < len(scope):
            prefix = table.get(".".join(scope[:depth]))
            if prefix is not None and prefix.kind == "class":
                continue
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


def _function_scope_prefixes(table: dict[str, Symbol], caller: str) -> set[str]:
    """Qualname prefixes of ``caller`` that denote function scopes.

    Class-body prefixes are excluded (bindings inside a class body do not
    apply to methods — Python name resolution skips class scopes).
    """
    prefixes: set[str] = set()
    parts = caller.split(".")
    for depth in range(len(parts), 0, -1):
        prefix = ".".join(parts[:depth])
        sym = table.get(prefix)
        if sym is None or sym.kind != "class":
            prefixes.add(prefix)
    return prefixes


def _applicable_binding(
    repo: _Repo, file: str, name: str, caller: str
) -> ImportBinding | None:
    """The import binding that ``name`` refers to at ``caller``, if any.

    Module-level bindings always apply; function-local ones only inside the
    def they live in. Innermost scope wins; on equal scope the later import
    wins (≈ rebinding order).
    """
    table = repo.symbols.get(file) or {}
    allowed = _function_scope_prefixes(table, caller)
    options = [
        binding
        for binding in repo.bindings_by_name.get(file, {}).get(name, [])
        if binding.scope is None or binding.scope in allowed
    ]
    if not options:
        return None
    return max(options, key=lambda b: (len(b.scope or ""), b.line))


def _class_owned(repo: _Repo, file: str, qualname: str) -> bool:
    """True when any proper prefix of ``qualname`` is a class symbol.

    Such symbols live in a class namespace — unreachable by the scope-blind
    unique-name fallback, so they are excluded from it.
    """
    table = repo.symbols.get(file) or {}
    parts = qualname.split(".")
    for depth in range(1, len(parts)):
        sym = table.get(".".join(parts[:depth]))
        if sym is not None and sym.kind == "class":
            return True
    return False


def _resolve_call(
    repo: _Repo,
    caller_file: str,
    callee: str,
    caller: str,
    stats: dict[str, int],
) -> tuple[tuple[str, str] | None, bool]:
    """Resolve one call site; returns ``((file, qualname) | None, weak)``."""
    parts = callee.split(".")
    if len(parts) > 1 and parts[0] in _SELF_NAMES:
        hit = _self_lookup(repo, caller_file, caller, parts[1:])
        if hit:
            stats["exact"] += 1
        return hit, False

    if len(parts) == 1:
        hit = _scoped_lookup(repo, caller_file, caller, parts)
        if hit:
            stats["exact"] += 1
            return hit, False
        binding = _applicable_binding(repo, caller_file, parts[0], caller)
        if binding:
            hit = _resolve_symbol_path(repo, caller_file, binding.target, strict=True)
            if hit:
                stats["exact"] += 1
            return hit, False
        candidates = [
            qual
            for qual in repo.by_name.get(caller_file, {}).get(parts[0], [])
            if not _class_owned(repo, caller_file, qual)
        ]
        if candidates:
            if len(candidates) == 1:
                stats["weak_unique"] += 1
                return (caller_file, candidates[0]), True
            stats["ambiguous"] += 1
            return None, False
        return None, False

    # Dotted callee, not self/cls.
    binding = _applicable_binding(repo, caller_file, parts[0], caller)
    if binding:
        target = f"{binding.target}.{'.'.join(parts[1:])}"
        hit = _resolve_symbol_path(repo, caller_file, target, strict=True)
        if hit:
            stats["exact"] += 1
        return hit, False

    # A locally defined class used as an attribute base: ``Widget.make()``.
    hit = _scoped_lookup(repo, caller_file, caller, parts)
    if hit:
        stats["exact"] += 1
        return hit, False

    # Ring 4: absolute module path with no binding recorded — a weak guess.
    hit = _resolve_symbol_path(repo, caller_file, callee, strict=False)
    if hit:
        stats["weak_module"] += 1
        return hit, True
    return None, False


def resolve_calls(
    parsed_by_file: dict[str, ParsedModule], python_files: list[str]
) -> tuple[list[dict], dict[str, int]]:
    """Resolve every recorded call site into aggregated CALLS rows + stats.

    Returns ``(rows, stats)`` where rows carry ``caller/callee file+qualname``
    (plus count, sorted lines, weak flag) ready for the graph writer, and
    stats report coverage: ``call_sites``, ``exact``, ``weak_unique``,
    ``weak_module``, ``unresolved``, ``ambiguous`` (a subset of unresolved —
    >1 non-class-scoped same-file name match). Deterministic: iterate files
    sorted, aggregate per caller->callee pair, sort rows by key.
    """
    repo = _Repo(parsed_by_file, sorted(python_files))
    aggregated: dict[tuple[str, str, str, str], list[int]] = defaultdict(list)
    weak_flags: dict[tuple[str, str, str, str], bool] = {}
    stats = {
        "call_sites": 0,
        "exact": 0,
        "weak_unique": 0,
        "weak_module": 0,
        "unresolved": 0,
        "ambiguous": 0,
    }

    for path in sorted(parsed_by_file):
        module = parsed_by_file[path]
        for call in module.calls:
            stats["call_sites"] += 1
            hit, weak = _resolve_call(repo, path, call.callee, call.caller, stats)
            if hit is None:
                stats["unresolved"] += 1
                continue
            key = (path, call.caller, hit[0], hit[1])
            aggregated[key].append(call.line)
            weak_flags[key] = weak_flags.get(key, True) and weak

    rows = [
        {
            "caller_file": key[0],
            "caller_qual": key[1],
            "callee_file": key[2],
            "callee_qual": key[3],
            "count": len(lines),
            "lines": sorted(lines),
            "weak": weak_flags[key],
        }
        for key, lines in sorted(aggregated.items())
    ]
    return rows, stats
