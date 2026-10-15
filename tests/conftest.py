"""Shared test setup: ``src/`` importability + FalkorDB-backed fixtures.

The integration fixtures live here (not in one test module) so every
integration test can share one throwaway graph and one skip rule.
"""

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from archivist.graph import GraphWriter


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
    """A scratch graph, wiped before and after each test."""
    host, port = falkordb_or_skip
    name = "test:archivist"
    writer = GraphWriter(host=host, port=port, graph=name)
    writer.delete()
    yield name
    writer.delete()
