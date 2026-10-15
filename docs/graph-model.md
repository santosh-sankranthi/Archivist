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
