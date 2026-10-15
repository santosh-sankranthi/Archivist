# Graph Model — v0 (draft, will evolve)

One graph per repo (e.g. `repo:flask`). Multi-repo = separate graphs (multi-tenancy demo).

## Nodes
| Label | Key props |
|---|---|
| `Repo` | name, path, head, updated_at |
| `Commit` | sha, author_name, ts, message, summary, seq (git-log position; stable tie-break for same-second timestamps) |
| `Author` | name, email, commits_count |
| `File` | path, lang, loc |
| `Symbol` | name, kind (function/class/method), qualname, file, lineno |
| `Module` | name (python package/module) |
| `Episode` | id, kind (agent-session/ingest-run/test-run/…), source, ts, created_at |
| `Fact` | id, text, kind, created_at, valid_at, invalid_at, expired_at |

## Edges
- `(Author)-[:AUTHORED]->(Commit)`
- `(Commit)-[:PARENT]->(Commit)`
- `(Commit)-[:MODIFIED {added, removed}]->(File)`
- `(Repo)-[:HEAD]->(Commit)`
- `(File)-[:DEFINES]->(Symbol)`
- `(File)-[:IMPORTS {line}]->(Module)`
- `(Symbol)-[:CALLS {count, lines, weak}]->(Symbol)` — aggregated per caller→callee pair;
  `lines` = call sites, `count` = len(lines), `weak` = guess flag (below);
  best-effort resolution (below)
- `(Episode)-[:PRODUCED]->(Fact)` — which session/run established a memory fact
- `(Fact)-[:ABOUT]->(Symbol|File|Commit)` — optional anchors, resolved by key
- `(Fact)-[:SUPERSEDES]->(Fact)` — lineage when a claim replaces another
- `(Episode)-[:HANDOFF {note, created_at}]->(Episode)` — work passed between
  sessions (M3b crew phases: planner → investigator → verifier); the
  coordination history is queryable like everything else

## Memory model (M3a) — episodes + bi-temporal facts

Everything the agent learns hangs off an `:Episode` — a raw unit that
really happened (`agent-session`, `ingest-run`, `test-run`, …); facts
reference the episode that produced them, so provenance exists by
construction. Fact windows are **bi-temporal**: `valid_at`/`invalid_at` =
event time (when the claim was true in the world), `created_at`/
`expired_at` = transaction time (when the graph learned it, and learned it
stopped being true).

Facts are **never deleted**: a contradiction closes the old window
(`invalid_at`, `expired_at`) and records the replacement with a tiled
`valid_at` + `SUPERSEDES` edge. `RepoGraph.recall` reads three views — true
now (open windows, default), full history (`include_invalid=true`), and
"true at T" (`as_of`, event time). Fact ids are content-addressed over
episode + normalized text unless given explicitly: remembering the same
claim twice converges, same rule as ingest.

Crew runs (M3b) record each phase as an `agent-session` episode with
`role` (planner/investigator/verifier) and `run` props, and link consecutive
phases with `HANDOFF` edges carrying the passing note — so "who worked on
this, in what order, passing what" is one query away.

## Rules
- Re-ingest = `MERGE` + prune (idempotent).
- Memory nodes (`Episode`/`Fact`) carry no run stamp: ingest's prune never
touches them, and superseded facts stay queryable (invalidate-don't-delete).
- Keys are file-scoped; symbol identity = `file + qualname`.
- All timestamps UTC epoch.

## CALLS resolution (M1c v1.1 — best-effort, honest)

Call *capture* is syntactic (`pyparse`): every call whose callee is a plain
dotted name (`f`, `self.m`, `os.path.join`), attributed to the nearest
enclosing def. *Resolution* happens repo-wide (`archivist.calls`), in
widening rings, first match worth trusting wins — everything else is
counted, never guessed. Rings 3–4 are guesses and carry `weak=true`:

1. **Same file, function scopes only** — `<caller-scope-prefix>.<name>` from
   the innermost scope outward; class scopes are skipped (Python name
   resolution never enters them), and `self.m`/`cls.m` calls use the
   enclosing-class floor (a class sibling or nested def, never a same-named
   top-level def).
2. **Cross-file via import bindings** — `import` / `from ... import`
   targets resolved against a module index built from file paths (longest
   module prefix wins; package `__init__.py` re-exports chased up to 2
   hops); relative imports anchored to the caller's directory. Bindings
   respect scope: a function-local import is only visible inside its own
   def. Import targets only match module keys that could genuinely be
   importable names (a packaged prefix is not a sys.path root).
3. **Same file, unique name — weak** — exactly one non-class-scoped symbol
   carries the name (`weak_unique`; scope-blind).
4. **Absolute module path, no binding — weak** — longest module key +
   symbol lookup (`weak_module`; right by naming luck, not semantics, e.g.
   `app.test_client` in test fixtures).

Edges are aggregated (`count`, `lines`, `weak`), carry the run stamp, and
are pruned with the nodes. Coverage on flask (2026-10-15, resolver v1.1):
**3,651 sites → 898 resolved (738 exact + 160 weak: 2 unique + 158 module)
→ 752 aggregated edges**; 2,753 unresolved; 0 ambiguous.

Known limits (honest):
- No type inference: `app.route` where `app` is a local variable never links
  (the largest remaining bucket); a resolver v2 could add local-alias typing
  (`x = Klass(...)`) — candidate for M2+.
- Ring-3/4 matches can be wrong in principle; every such edge is flagged
  `weak` so answers can down-rank or filter them (flask keeps two weak
  ring-4 self-loops in `cli.py` as documented guesses).
- `super()`, methods passed as values, dynamic `getattr`, and calls in
  module-level lambdas are out of scope.

## v0 implementation notes

- All writes are idempotent MERGEs keyed on stable identities (path, sha,
  email, file+qualname) — re-ingest converges instead of duplicating.
- Prune: File/Symbol/Module nodes carry a per-run stamp; stale nodes are
  deleted after each ingest. Commit/Author nodes are append-only (history
  never shrinks on a normal re-ingest).
- Diffs come from `git --numstat` with `--no-renames`: merge commits carry no
  diff (git default), so MODIFIED edges skip merges; renames appear as
  delete+add pairs.
- File nodes cover the current tree plus history-only paths (stub nodes with
  no `lang`/`loc`) so old MODIFIED edges stay meaningful.
- Symbols: Python only, stdlib `ast` — classes, functions (incl. nested),
  methods. Lambdas and assigned callables are out of scope for v0.
- Call sites: plain dotted-name callees attributed to the nearest enclosing
  def; resolution rules and coverage above.
- Timestamps are UTC epoch; `run_id` is the ingest run stamp.
