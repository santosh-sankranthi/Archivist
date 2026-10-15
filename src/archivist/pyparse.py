"""Python source parsing for ingest v0.

Why stdlib ``ast`` (not tree-sitter, not regex): deterministic output, zero
extra dependencies, and enough for v0's needs — files, symbols, imports.
tree-sitter can replace this later if multi-language support is required.

Call sites (M1c) are recorded *syntactically*: every call whose callee is a
plain dotted name (``f``, ``self.m``, ``os.path.join``) with the nearest
enclosing def as its caller. What the name actually points at is resolved
repo-wide later (``archivist.calls``). Calls through anything else
(``foo().bar``, subscripts; lambdas at module level have no def to attribute)
are skipped by design — resolution only ever links real Symbols.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Symbol:
    """A definition worth tracking: classes, functions, methods, nested defs."""

    name: str
    kind: str  # "class" | "function" | "method"
    qualname: str
    lineno: int


@dataclass(frozen=True)
class ImportRef:
    """A single ``import`` / ``from ... import`` target with its source line."""

    module: str
    line: int


@dataclass(frozen=True)
class ImportBinding:
    """A name bound by an import, as used in code.

    ``name`` is the identifier that appears in source (the alias wins);
    ``target`` is the dotted path it refers to, with leading dots kept for
    relative imports. ``import a.b`` binds the top-level name ``a`` (the
    remaining dotted path is filled in from the callee), while
    ``import a.b as c`` / ``from m import x [as y]`` bind the full target.
    Wildcard imports bind nothing (namespace unknown without the module).
    ``scope`` records *where* the import lives: ``None`` = module level, else
    the qualname of the enclosing def. Function-local imports are only
    visible inside their own def (collapsing them file-wide resolved names
    through the wrong target — review finding F2).
    """

    name: str
    target: str
    line: int
    scope: str | None = None


@dataclass(frozen=True)
class CallRef:
    """A call site shaped like a dotted name, attributed to its caller.

    ``callee`` is the text as written (``self.step``, ``flask.abort``);
    ``caller`` is the qualname of the nearest enclosing def, so it matches
    ``Symbol.qualname`` exactly.
    """

    callee: str
    line: int
    caller: str


@dataclass
class ParsedModule:
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[ImportRef] = field(default_factory=list)
    bindings: list[ImportBinding] = field(default_factory=list)
    calls: list[CallRef] = field(default_factory=list)


def parse_python(source: str) -> ParsedModule:
    """Parse Python source code into symbols and imports.

    Output order follows source order (deterministic for a given input).
    Raises ``SyntaxError`` on invalid input; callers degrade per file.
    """
    tree = ast.parse(source)
    parsed = ParsedModule()
    _collect_defs(tree.body, prefix="", parent_kind="module", out=parsed.symbols)
    _collect_imports(tree, parsed.imports)
    _ScopeVisitor(parsed.calls, parsed.bindings).visit(tree)
    # Sort for deterministic output regardless of visitor traversal order.
    parsed.calls.sort(key=lambda call: (call.line, call.callee))
    parsed.bindings.sort(key=lambda binding: (binding.line, binding.name))
    return parsed


# Statement kinds that contain nested statement lists we must descend into,
# so definitions behind `if TYPE_CHECKING:` / `try / except ImportError:` are
# still discovered. TryStar covers `except*` blocks (Python 3.11+); Match
# covers `match`/`case` bodies (Python 3.10+).
_CONTAINER_STMTS: tuple[type[ast.stmt], ...] = (
    ast.If,
    ast.Try,
    ast.TryStar,
    ast.With,
    ast.AsyncWith,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Match,
)


def _collect_defs(
    body: list[ast.stmt], prefix: str, parent_kind: str, out: list[Symbol]
) -> None:
    """Collect defs in source order, descending into conditional blocks.

    Nested functions keep their outer name in ``qualname`` (``top.inner``);
    functions defined directly under a class get kind ``method``.
    """
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qual = prefix + node.name
            kind = "method" if parent_kind == "class" else "function"
            out.append(Symbol(node.name, kind, qual, node.lineno))
            _collect_defs(node.body, qual + ".", "function", out)
        elif isinstance(node, ast.ClassDef):
            qual = prefix + node.name
            out.append(Symbol(node.name, "class", qual, node.lineno))
            _collect_defs(node.body, qual + ".", "class", out)
        elif isinstance(node, _CONTAINER_STMTS):
            for sub_body in _sub_bodies(node):
                _collect_defs(sub_body, prefix, parent_kind, out)


def _sub_bodies(node: ast.stmt) -> list[list[ast.stmt]]:
    """Statement lists nested inside a container statement.

    Covers ``handlers`` (Try) and ``cases`` (Match) beyond the plain body
    attributes, so defs inside those blocks are found too.
    """
    bodies: list[list[ast.stmt]] = []
    for attr in ("body", "orelse", "finalbody"):
        value = getattr(node, attr, None)
        if isinstance(value, list):
            bodies.append(value)
    for handler in getattr(node, "handlers", None) or []:
        bodies.append(handler.body)
    for case in getattr(node, "cases", None) or []:
        bodies.append(case.body)
    return bodies


def _collect_imports(tree: ast.Module, out: list[ImportRef]) -> None:
    """Collect import targets via ``ast.walk`` (breadth-first traversal order
    carries no meaning; line numbers are kept and order is not relied on)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append(ImportRef(module=alias.name, line=node.lineno))
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            out.append(ImportRef(module=module, line=node.lineno))


def _dotted(node: ast.expr) -> str | None:
    """Dotted text for ``Name``/``Attribute`` chains; ``None`` otherwise.

    ``os.path.join`` -> ``"os.path.join"``; ``foo().bar`` and ``x[0].y``
    yield ``None`` (the base is not a plain name, so the target is not a
    symbol we could ever link).
    """
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


class _ScopeVisitor(ast.NodeVisitor):
    """One walk, recording call sites and import bindings with their scope.

    Both need the same thing: the qualname of the nearest enclosing def/class
    (module level = no scope). Mirrors ``_collect_defs`` qualname rules, so
    recorded callers and binding scopes always name real Symbols. Imports
    inside a def are recorded with that def's qualname as ``scope`` so the
    resolver can keep them function-local (review finding F2).
    """

    def __init__(self, calls: list[CallRef], bindings: list[ImportBinding]) -> None:
        self._calls = calls
        self._bindings = bindings
        self._scope: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._scoped(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._scoped(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scoped(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.asname:
                self._record(alias.asname, alias.name, node.lineno)
            else:
                top = alias.name.split(".", 1)[0]
                self._record(top, top, node.lineno)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = "." * node.level + (node.module or "")
        prefix = base + "." if base and not base.endswith(".") else base
        for alias in node.names:
            if alias.name == "*":
                continue
            self._record(
                alias.asname or alias.name, f"{prefix}{alias.name}", node.lineno
            )

    def _record(self, name: str, target: str, line: int) -> None:
        scope = ".".join(self._scope) if self._scope else None
        self._bindings.append(
            ImportBinding(name=name, target=target, line=line, scope=scope)
        )

    def _scoped(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
    ) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_Call(self, node: ast.Call) -> None:
        if self._scope:
            callee = _dotted(node.func)
            if callee:
                self._calls.append(
                    CallRef(
                        callee=callee, line=node.lineno, caller=".".join(self._scope)
                    )
                )
        self.generic_visit(node)  # nested calls inside arguments too
