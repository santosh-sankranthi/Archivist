## For judges — how to test Archivist in 3 minutes

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
