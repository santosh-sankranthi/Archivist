"""Call-resolution tests (M1c) — pure units, no FalkorDB needed.

Each case pins one resolution rule: scoped same-file lookup, unique-name
weak fallback, ambiguous counting, import-binding cross-file resolution, the
class-attribute path, and the ``self`` scope floor (no leaking to top level).
"""

from __future__ import annotations

from archivist import pyparse
from archivist.calls import resolve_calls

SAME_FILE = """\
def main():
    step()
    data = fetch()
    step()
    return format(data)


def step():
    pass


def outer():
    def inner():
        pass

    return inner()


def fetch():
    return 1


def format(value):
    return str(value)


class Widget:
    def render(self):
        self.emit()

    def emit(self):
        self.render()
"""

CROSS_FILE = {
    "pkg/util.py": """\
def helper():
    return 1


class Widget:
    def render(self):
        return 2
""",
    "pkg/core.py": """\
from .util import helper as h
from .util import Widget
from . import util


def run():
    h()
    Widget()
    util.helper()
""",
}

WEAK = """\
def setup():
    helper()


def container():
    def helper():
        pass

    return helper
"""

AMBIGUOUS = """\
def a():
    dup()


def b():
    def dup():
        pass


def c():
    def dup():
        pass
"""

SELF_FLOOR = """\
def step():
    pass


class A:
    def run(self):
        self.step()
"""

CLASS_ATTR = """\
class Widget:
    @classmethod
    def make(cls):
        return cls()


def build():
    return Widget.make()
"""

REEXPORT = {
    "pkg/__init__.py": "from .impl import Widget\n",
    "pkg/impl.py": "class Widget:\n    def render(self):\n        pass\n",
    "app.py": "import pkg\n\n\ndef build():\n    return pkg.Widget()\n",
    "app2.py": "from pkg import Widget\n\n\ndef make():\n    return Widget()\n",
}

CLASS_SCOPE = """\
class Tagged:
    def dumps(self, value):
        return dumps(value)

    def helper(self):
        pass

    def run(self):
        helper()
"""

CLASS_SCOPE_BINDING = {
    "pkg/json.py": "def dumps(value):\n    return value\n",
    "pkg/tag.py": (
        "from .json import dumps\n"
        "\n"
        "\n"
        "class Tagged:\n"
        "    def dumps(self, value):\n"
        "        return dumps(value)\n"
    ),
}

SCOPED_BINDINGS = {
    "pkg/testing.py": "class A:\n    pass\n\n\nclass B:\n    pass\n",
    "pkg/app.py": (
        "class App:\n"
        "    def test_a(self):\n"
        "        from .testing import A as cls\n"
        "        return cls()\n"
        "\n"
        "    def test_b(self):\n"
        "        from .testing import B as cls\n"
        "        return cls()\n"
    ),
}

STRICT_ROOT = {
    "pkg/__init__.py": "",
    "pkg/json.py": "def dumps(value):\n    return value\n",
    "app.py": "import json\n\n\ndef run():\n    return json.dumps(1)\n",
}

MATCH_DEFS = """\
def dispatch(x):
    match x:
        case 1:
            def inner():
                return 1

            return inner()
        case _:
            return 0
"""


def _resolve(sources: dict[str, str]):
    parsed = {path: pyparse.parse_python(src) for path, src in sources.items()}
    rows, stats = resolve_calls(parsed, list(sources))
    edges = {
        (
            r["caller_file"],
            r["caller_qual"],
            r["callee_file"],
            r["callee_qual"],
            r["count"],
        )
        for r in rows
    }
    return edges, stats


def _one_file(src: str):
    return _resolve({"app.py": src})


def test_same_file_scoped_resolution():
    edges, stats = _one_file(SAME_FILE)
    assert edges == {
        ("app.py", "main", "app.py", "step", 2),
        ("app.py", "main", "app.py", "fetch", 1),
        ("app.py", "main", "app.py", "format", 1),
        ("app.py", "outer", "app.py", "outer.inner", 1),
        ("app.py", "Widget.render", "app.py", "Widget.emit", 1),
        ("app.py", "Widget.emit", "app.py", "Widget.render", 1),
    }
    # 8 sites: 7 resolved exactly, 1 unresolved builtin (str).
    assert stats == {
        "call_sites": 8,
        "exact": 7,
        "weak_unique": 0,
        "weak_module": 0,
        "unresolved": 1,
        "ambiguous": 0,
    }


def test_cross_file_via_imports():
    edges, stats = _resolve(CROSS_FILE)
    assert edges == {
        # h() and util.helper() are the same target — aggregated, count 2.
        ("pkg/core.py", "run", "pkg/util.py", "helper", 2),
        ("pkg/core.py", "run", "pkg/util.py", "Widget", 1),
    }
    assert stats["call_sites"] == 3
    assert stats["exact"] == 3
    assert stats["unresolved"] == 0

    # Two call sites of the same pair aggregate into one row with both lines.
    agg_sources = {
        "pkg/core.py": "from .util import helper as h\n\n\ndef run():\n    h()\n    h()\n",
        "pkg/util.py": "def helper():\n    return 1\n",
    }
    agg_parsed = {path: pyparse.parse_python(src) for path, src in agg_sources.items()}
    rows, _ = resolve_calls(agg_parsed, list(agg_sources))
    assert len(rows) == 1
    assert rows[0]["count"] == 2
    assert rows[0]["lines"] == [5, 6]


def test_unique_name_is_weak():
    edges, stats = _one_file(WEAK)
    assert edges == {("app.py", "setup", "app.py", "container.helper", 1)}
    assert stats == {
        "call_sites": 1,
        "exact": 0,
        "weak_unique": 1,
        "weak_module": 0,
        "unresolved": 0,
        "ambiguous": 0,
    }


def test_ambiguous_name_left_unresolved():
    edges, stats = _one_file(AMBIGUOUS)
    assert edges == set()
    assert stats["call_sites"] == 1
    assert stats["unresolved"] == 1
    assert stats["ambiguous"] == 1


def test_self_never_leaks_to_top_level():
    edges, stats = _one_file(SELF_FLOOR)
    assert edges == set()  # `step` exists top-level, but self.step must not link
    assert stats["unresolved"] == 1
    assert stats["exact"] == 0


def test_class_attribute_call():
    edges, stats = _one_file(CLASS_ATTR)
    assert edges == {("app.py", "build", "app.py", "Widget.make", 1)}
    assert stats["exact"] == 1
    assert stats["unresolved"] == 1  # cls() — no type to resolve against


def test_reexport_via_package_init():
    """``pkg.Widget`` / ``from pkg import Widget`` chase __init__ re-exports."""
    edges, stats = _resolve(REEXPORT)
    assert edges == {
        ("app.py", "build", "pkg/impl.py", "Widget", 1),
        ("app2.py", "make", "pkg/impl.py", "Widget", 1),
    }
    assert stats["exact"] == 2
    assert stats["unresolved"] == 0


def test_bare_names_never_leak_into_class_scope():
    """Review F1: ``dumps()``/``helper()`` in a class must not resolve to
    class members; without a binding they stay unresolved."""
    edges, stats = _one_file(CLASS_SCOPE)
    assert edges == set()
    assert stats == {
        "call_sites": 2,
        "exact": 0,
        "weak_unique": 0,
        "weak_module": 0,
        "unresolved": 2,
        "ambiguous": 0,
    }


def test_binding_wins_over_class_scope_leak():
    """Review F1: with ``from .json import dumps``, the bare call resolves to
    the imported symbol (not the sibling method) — tag.py's real pattern."""
    edges, stats = _resolve(CLASS_SCOPE_BINDING)
    assert edges == {("pkg/tag.py", "Tagged.dumps", "pkg/json.py", "dumps", 1)}
    assert stats["exact"] == 1
    assert stats["unresolved"] == 0


def test_function_local_bindings_stay_local():
    """Review F2: two ``... as cls`` imports in different methods resolve to
    their own targets, not to whichever import the tree walk saw last."""
    edges, stats = _resolve(SCOPED_BINDINGS)
    assert edges == {
        ("pkg/app.py", "App.test_a", "pkg/testing.py", "A", 1),
        ("pkg/app.py", "App.test_b", "pkg/testing.py", "B", 1),
    }
    assert stats["exact"] == 2
    assert stats["unresolved"] == 0


def test_strict_import_targets_skip_packaged_roots():
    """A packaged prefix is not a sys.path root: ``json.dumps`` must not
    resolve into ``pkg/json.py`` when ``pkg`` is a package (it would resolve
    without the package marker)."""
    edges, stats = _resolve(STRICT_ROOT)
    assert edges == set()
    assert stats["unresolved"] == 1

    without_package_marker = {
        key: value for key, value in STRICT_ROOT.items() if key != "pkg/__init__.py"
    }
    edges2, _ = _resolve(without_package_marker)
    assert ("app.py", "run", "pkg/json.py", "dumps", 1) in edges2


def test_defs_and_calls_inside_match_statements():
    """Review F4: defs nested in ``match``/``case`` bodies are collected and
    resolve as scope-valid candidates."""
    parsed = pyparse.parse_python(MATCH_DEFS)
    quals = {symbol.qualname for symbol in parsed.symbols}
    assert {"dispatch", "dispatch.inner"} <= quals

    edges, stats = _one_file(MATCH_DEFS)
    assert edges == {("app.py", "dispatch", "app.py", "dispatch.inner", 1)}
    assert stats["exact"] == 1
