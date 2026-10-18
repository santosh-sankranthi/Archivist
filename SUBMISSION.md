# Archivist — one community brain, three surfaces, one graph

**Tagline:** A hosted maintainer agent for GitHub + Slack + web, whose
memory *is* a FalkorDB graph — every answer cited, every conversation
remembered, every surface sharing the same brain.

## The problem

Open-source communities repeat themselves: the same questions, the same
triage work, the same "have we seen this before?" — while the answers sit
scattered across issues, chats, docs and git history. Bots that answer from
vector stores forget between sessions and can't show why they believe
something.

## What we built (all live, all testable)

1. **GitHub maintainer agent** — deployed on a fork of
   `FalkorDB/falkordb-py`. New issue → grounded reply with citations
   (file + symbol references; docs.falkordb.com URLs), first-time-author
   welcome, triage labels, related-issue links, security-report redirect,
   and every answer stored as a memory episode. Mentions (`@archivist`)
   in comments get in-thread answers too.
2. **Slack agent** — the same core behind the Events API: `@archivist`
   mention → in-thread answer, written to the same memory.
3. **Web buddy** — the deployed console: ask the same brain from the
   browser, instantly.
4. **The graph is the product** — `repo:falkordb-py` holds the ingested
   code (65 files, 56 modules, 426 symbols, 509 commits, 21 authors) plus
   **1,541 documentation sections** as `DocPage` nodes, plus *interaction
   memory*: episodes tagged by surface (`github-answer`, `slack-answer`),
   first-seen markers, bi-temporal facts with provenance edges. Ask
   surface A about a thread from surface B — same graph, same recall.

## How FalkorDB is used (the point)

- **One graph for code + docs + memory**: multi-hop questions ("which
  commits touched the file that defines this symbol the docs mention?")
  are just Cypher — no vector store sidecar.
- **Adapters write back**: labels, replies, episode nodes, first-seen
  markers — the graph is updated by *social interaction*, not only by
  batch ingest.
- **`search_docs` tool**: phrase-first, keyword-fallback ranking over the
  1,541 `DocPage` chunks, returning citable URLs the agent must use.
- **Auditable agent loop**: every answer carries its full trace — tools
  used, Cypher executed, rows returned, ms — rendered in the console.

## Stack

Python (stdlib-first webapp + agent core), FalkorDB, OpenAI-compatible
free-tier models (primary + rotating fallback on empty finalizations),
HMAC-verified webhooks for GitHub and Slack, FF-only CI deploying from a
self-hosted runner.

## How to test it (3 minutes)

1. **GitHub:** open an issue on `santosh-sankranthi/falkordb-py` — any
   real FalkorDB question. Watch the agent reply with citations.
2. **Web:** ask the console the same class of question — same brain,
   different door.
3. **Slack:** mention `@archivist` in the workspace — in-thread answer.
4. **The money shot:** ask a docs question on GitHub, then ask the web
   buddy about *"the vector-index question from GitHub"* — it recalls the
   episode, because memory is graph state, not chat history.

## Prior art & positioning (being honest)

The space has great tools: Sweep (issue→PR bot, archived 2025),
PR-Agent/Qodo (PR review), Onyx/Danswer (docs Q&A, heavyweight RAG stack),
Khoj (personal assistant), AnswerOverflow (Discord answers). They are
excellent at *answering*. **None uses a graph database as cross-surface
memory, and none carries provenance into every sentence.** Archivist is
deliberately not a code-writing agent: it is the maintainer's memory —
triage, recall, connection-finding — because that is what a graph does
best.

## Roadmap

GitHub App packaging (replacing the PAT), duplicate detection via graph
embeddings, a weekly community brief, richer web→GitHub action hand-offs,
multi-repo communities.
