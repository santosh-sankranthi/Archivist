# Archivist

**Graph Hacks 2026 submission (WeMakeDevs × FalkorDB) — work in progress.**

An engineering-intelligence agent with FalkorDB as its context layer: it turns
a codebase + its git history into a living graph, answers multi-hop questions
with traceable reasoning paths, takes actions, remembers across sessions, and
coordinates multiple agents over shared graph state.

Status: **M2 — agent core in progress** (ingest pipeline repeatable & idempotent;
read-only graph tool suite with envelope+trace contract; tool-calling agent loop
with auditable traces — see `docs/demo/`).

## Quickstart (dev)

Requires Python ≥ 3.11 and a FalkorDB instance on `127.0.0.1:6379`
(pin `falkordb/falkordb:v4.22.1` in production — what this project is tested on):

```bash
docker run -p 6379:6379 --name falkordb -d falkordb/falkordb:v4.22.1
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

## Ask questions (agent loop, BYO LLM key)

The agent answers questions by calling read-only graph tools and every answer
carries its full trace (tools called, Cypher run, timings). Any
OpenAI-compatible endpoint works — configure via environment or `.env`:

```bash
ARCHIVIST_LLM_BASE_URL=https://api.openai.com/v1   # or opencode zen, OpenRouter, vLLM, ...
ARCHIVIST_LLM_API_KEY=sk-...
ARCHIVIST_LLM_MODEL=gpt-4o-mini

python scripts/ask.py "who calls url_for?"          # rendered answer + trace
python scripts/ask.py "why is blueprints.py careful?" --json   # machine-readable
```

`OPENAI_BASE_URL` / `OPENAI_API_KEY` / `OPENAI_MODEL` are accepted as
fallbacks; keys never enter the repo. A mechanics transcript generated without
a key lives in `docs/demo/m2b-agent-loop.txt` (scripted calls, real queries).

> Built during the Graph Hacks event window (Oct 15–18, 2026).
