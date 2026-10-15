# Archivist

**Graph Hacks 2026 submission (WeMakeDevs × FalkorDB) — work in progress.**

An engineering-intelligence agent with FalkorDB as its context layer: it turns
a codebase + its git history into a living graph, answers multi-hop questions
with traceable reasoning paths, takes actions, remembers across sessions, and
coordinates multiple agents over shared graph state.

Status: **M3 — memory + multi-agent** (ingest pipeline repeatable & idempotent;
read-only graph tool suite with envelope+trace contract; tool-calling agent
loop with auditable traces; M3a: episodes + bi-temporal facts — sessions
recall each other, invalidate-don't-delete; M3b: planner → investigator →
verifier crew with handoffs as graph state — see `docs/demo/`).

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
fallbacks; keys never enter the repo. Transient endpoint failures (429/5xx,
timeouts) are retried with backoff; anything that still fails is recorded on
the answer's trace instead of crashing the run. A mechanics transcript
generated without a key lives in `docs/demo/m2b-agent-loop.txt` (scripted
calls, real queries).

## Memory (M3)

Every `ask.py` run is persisted to the graph as an `agent-session` episode,
and the agent can `remember` findings (anchored to symbols/files/commits)
and `recall` them later — facts carry bi-temporal validity windows, and a
superseded fact is closed, never deleted. `--no-memory` skips recording.
Mechanics transcript: `docs/demo/m3a-memory.txt` (scripted calls, live
graph, a second session recalling the first).

## Multi-agent crew (M3b)

One question, three roles over the same graph: the **planner** drafts an
executable investigation plan, the **investigator** runs it with the full
tool suite, and the **verifier** re-runs the decisive checks and owns the
final answer. Role tool scopes are enforced, not suggested (the verifier
cannot write memory); each phase is recorded as an `agent-session` episode
and the handoffs are `(:Episode)-[:HANDOFF]->(:Episode)` edges — the crew's
coordination history is queryable like everything else.

```bash
python scripts/crew.py "what breaks if url_for changes, and who should review it?"
```

Mechanics transcript: `docs/demo/m3b-handoffs.txt` (scripted model, live
graph: plan → investigation → independent re-checks, handoff chain read
back out of the graph).

> Built during the Graph Hacks event window (Oct 15–18, 2026).
