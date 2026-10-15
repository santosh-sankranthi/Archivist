# Demo questions — draft v0 (pin at M2 close)

The questions the live demo must answer *well*. Each is multi-hop and
graph-native, must render its reasoning path + sources, and must be
checkable against the real `repo:flask` graph — no staged answers.
Trim to 6–8 for demo pacing; every kept question needs a rehearsed trace.

| # | Question | Beat | Tool path | Status |
|---|----------|------|-----------|--------|
| 1 | What breaks if `flask.helpers.url_for` changes? | impact | `callers` depth 2–3 (weak excluded) + `importers` | ✓ works (`m2a-tools.txt`) |
| 2 | Who should review a change to `src/flask/app.py`? | ownership | `owners` + `file_history` | ✓ works |
| 3 | Why does Flask keep `should_ignore_error`? | archaeology | `search_commits` → commit → `file_history` | ✓ search works; writeup pending |
| 4 | What does `Flask.full_dispatch_request` touch, 3 hops down? | comprehension | `callees` depth 3 | to verify |
| 5 | Which modules depend on `flask.ctx`? | impact breadth | `importers` + reverse walk | to verify |
| 6 | The riskiest hubs: highest fan-in symbols in the codebase? | structure | `query` aggregation over CALLS | to verify |
| 7 | Which tests exercise `test_client` (blast radius before refactor)? | impact | `callers` + test-file split | to verify |
| 8 | Who knows `blueprints` best, and what did they change last there? | ownership | `owners` + `file_history` | ✓ works |
| 9 | When did `session` cookie handling last change, and why? | archaeology | `file_history` + `search_commits` | to verify |
| 10 | Shortest call path `Flask.wsgi_app` → `Flask.finalize_request`. | trace | shortestPath (WITH/RETURN form) | to verify |

Checkout questions the demo should also survive:
- "Add `x` — who uses it?" (unscripted judge question → `query` escape hatch)
- "Answer something about *this repo*" (dogfood: ingest archivist itself at M5)

Rules for pinning: real flask data only; every claim reproducible; say
"unknown" where the resolver is not confident (weak/unresolved) instead of
guessing.
