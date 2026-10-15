# Archivist

**Graph Hacks 2026 submission (WeMakeDevs × FalkorDB) — work in progress.**

An engineering-intelligence agent with FalkorDB as its context layer: it turns
a codebase + its git history into a living graph, answers multi-hop questions
with traceable reasoning paths, takes actions, remembers across sessions, and
coordinates multiple agents over shared graph state.

Status: **M1 — ingest pipeline working** (files, Python symbols, imports,
commits, authors, modification stats; repeatable & idempotent).

## Quickstart (dev)

Requires Python ≥ 3.11 and a FalkorDB instance on `127.0.0.1:6379`:

```bash
docker run -p 6379:6379 --name falkordb -d falkordb/falkordb:latest
```

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

git clone https://github.com/pallets/flask data/flask   # or any repo
python scripts/ingest.py                # -> graph "repo:flask"
pytest -q                               # unit + integration tests
```

Options: `python scripts/ingest.py --help` (`--repo`, `--graph`, `--rebuild`,
`--no-prune`, `--json`). v0 trade-offs and known limitations are documented in
`docs/graph-model.md`.

> Built during the Graph Hacks event window (Oct 15–18, 2026).
