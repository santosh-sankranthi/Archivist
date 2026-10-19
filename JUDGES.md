## For judges — how to test Archivist in 3 minutes

> **Full walkthrough:** the live [reader guide](https://figure-them-trucks-concentrate.trycloudflare.com/docs) — capabilities, per-surface guides, and the complete testing checklist with expected results.

**1. GitHub surface (the fork):** https://github.com/santosh-sankranthi/falkordb-py

Open a new issue with any real question about FalkorDB (e.g. *"How do I
create a vector index?"* or *"Why does my query leak connections?"*). Within
a minute the agent replies on the thread — grounded in this repo's code
graph plus 1,541 ingested documentation sections, every claim cited
(docs URLs, file + symbol references). First-time authors get a welcome;
bug reports get triage labels and a missing-info checklist.

**2. Web buddy (the same brain, instantly):**
https://figure-them-trucks-concentrate.trycloudflare.com/console/

Ask the same class of question in the console. Anonymous visits are
quota-limited so the demo stays fair for everyone.

Then press **"File as GitHub issue"** on any answer — the question lands
on the fork as a real issue and the hosted agent replies on the thread:
the web surface *acting* on the repository.

**3. Slack surface:** the workspace is open — join with
https://join.slack.com/t/demoworkspace-gam4651/shared_invite/zt-4dwhz22o0-5JfcMZ_qpBTN7aigAsvvkQ
then mention `@archivist` in a channel: answers arrive in-thread and
are stored in the same memory.

**The part that makes it a *graph* project, not a chatbot:** all three
surfaces share one FalkorDB graph. Ask the web buddy something you already
asked on GitHub, or reference an earlier thread ("last time you said…") —
the agent recalls it, because answers, first-seen authors, and facts are
graph nodes (episodes + bi-temporal facts) written by every surface and
read by every surface. The same graph also holds the ingested code
(`repo:falkordb-py`: files, modules, symbols, commits, authors) and the
documentation pages — so one query can walk from doc → symbol → commit.

## More to try (5 minutes)

- **Memory across surfaces (the USP):** ask the console or Slack
  *"what's the status of issue 3?"* — the answer cites the original
  GitHub thread by URL, because `recall` matches issue/PR numbers against
  the url recorded on each stored session.
- **Console surfaces:** the left rail lists every session from every
  surface (WEB / GH / SL badges) — click one for a read-only peek of the
  stored answer, or press **File as GitHub issue** on a fresh answer to
  bridge web → repo.
- **Graph views** (⌘K palette or deep links): History (commit time
  machine), Impact (blast-radius cone), Ownership (who knows what), Crew
  (planner → investigator → verifier handoffs).
- **Bring your own key:** Settings in the console — base URL, model, and
  key stay in your browser; the server never stores or logs them.
