# Graph Model — v0 (draft, will evolve)

One graph per repo (e.g. `repo:flask`). Multi-repo = separate graphs (multi-tenancy demo).

## Nodes
| Label | Key props |
|---|---|
| `Repo` | name, path, head, updated_at |
| `Commit` | sha, author_name, ts, message, summary |
| `Author` | name, email, commits_count |
| `File` | path, lang, loc |
| `Symbol` | name, kind (function/class/method), qualname, file, lineno |
| `Module` | name (python package/module) |

## Edges
- `(Author)-[:AUTHORED]->(Commit)`
- `(Commit)-[:PARENT]->(Commit)`
- `(Commit)-[:MODIFIED {added, removed}]->(File)`
- `(Repo)-[:HEAD]->(Commit)`
- `(File)-[:DEFINES]->(Symbol)`
- `(File)-[:IMPORTS {line}]->(Module)`
- `(Symbol)-[:CALLS {count, lines}]->(Symbol)` — aggregated per caller→callee pair;
  `lines` = call sites, `count` = len(lines); best-effort resolution (below)

## Rules
- Re-ingest = `MERGE` + prune (idempotent).
- Keys are file-scoped; symbol identity = `file + qualname`.
- All timestamps UTC epoch.

## CALLS resolution (M1c — best-effort, honest)

Call *capture* is syntactic (`pyparse`): every call whose callee is a plain
dotted name (`f`, `self.m`, `os.path.join`), attributed to the nearest
enclosing def. *Resolution* happens repo-wide (`archivist.calls`), in
widening rings, first confident match wins — everything else is counted,
never guessed:

1. **Same file, scope-aware** — `<caller-scope-prefix>.<name>` from the
   innermost scope outward; `self`/`cls` calls use the enclosing-class floor
   (a class sibling or nested def, never a same-named top-level def).
2. **Same file, unique name** — exactly one symbol with that name (a *weak*
   match: scope-blind; counted separately).
3. **Cross-file via imports** — dotted targets from `import` / `from ...`
   resolved against a module index built from file paths (longest module
   prefix wins; package `__init__.py` re-exports chased up to 2 hops);
   relative imports anchored to the caller's directory.

Edges are aggregated (`count`, `lines`), carry the run stamp, and are
pruned with the nodes. Coverage on flask (2026-10-15): **3,651 sites → 917
resolved (752 exact + 165 weak) → 765 aggregated edges**; 2,734 unresolved.

Known limits (honest):
- No type inference: `app.route` where `app` is a local variable never links
  (the largest remaining bucket); a resolver v2 could add local-alias typing
  (`x = Klass(...)`) — candidate for M2+.
- Weak matches can be wrong in principle and aren't flagged on the edge yet.
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
