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
- `(Symbol)-[:CALLS {line}]->(Symbol)` (v1+, name-based resolution)

## Rules
- Re-ingest = `MERGE` + prune (idempotent).
- Keys are file-scoped; symbol identity = `file + qualname`.
- All timestamps UTC epoch.

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
- Timestamps are UTC epoch; `run_id` is the ingest run stamp.
