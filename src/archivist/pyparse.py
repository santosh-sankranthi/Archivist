"""Python source parsing for ingest v0.

Why stdlib ``ast`` (not tree-sitter, not regex): deterministic output, zero
extra dependencies, and enough for v0's needs — files, symbols, imports.
tree-sitter can replace this later if multi-language support is required.
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


@dataclass
class ParsedModule:
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[ImportRef] = field(default_factory=list)


def parse_python(source: str) -> ParsedModule:
    """Parse Python source code into symbols and imports.

    Output order follows source order (deterministic for a given input).
    Raises ``SyntaxError`` on invalid input; callers degrade per file.
    """
    tree = ast.parse(source)
    parsed = ParsedModule()
    _collect_defs(tree.body, prefix="", parent_kind="module", out=parsed.symbols)
    _collect_imports(tree, parsed.imports)
    return parsed


# Statement kinds that contain nested statement lists we must descend into,
# so definitions behind `if TYPE_CHECKING:` / `try / except ImportError:` are
# still discovered. TryStar covers `except*` blocks (Python 3.11+).
_CONTAINER_STMTS: tuple[type[ast.stmt], ...] = (
    ast.If,
    ast.Try,
    ast.TryStar,
    ast.With,
    ast.AsyncWith,
    ast.For,
    ast.AsyncFor,
    ast.While,
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
    """Statement lists nested inside a container statement."""
    bodies: list[list[ast.stmt]] = []
    for attr in ("body", "orelse", "finalbody"):
        value = getattr(node, attr, None)
        if isinstance(value, list):
            bodies.append(value)
    for handler in getattr(node, "handlers", None) or []:
        bodies.append(handler.body)
    return bodies


def _collect_imports(tree: ast.Module, out: list[ImportRef]) -> None:
    """Collect import targets in source order (``ast.walk`` = breadth-first)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append(ImportRef(module=alias.name, line=node.lineno))
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            out.append(ImportRef(module=module, line=node.lineno))
