# Console v2 demo captures (M4R)

Re-shot **2026-10-17 ~05:35Z** in one full pass after the **Ownership**
view landed (the runway's who-knows-what ledger; the rail gained
"Ownership", so every rail-visible still was re-taken for consistency —
the pass wrote all 23 stills). Stills run against the live server
`:8787`; ask scenes (04–05) against the scripted capture server `:8789`
(restarted beforehand for a fresh scenario run, stopped after) — the real
loop, tools and graph, only the model's next-move choices are canned (the
status chip reads "scripted (demo)" inside the shot itself). The capture
script fails on any JS error; this pass was clean.

Notes:

- `17-ownership.png` … `20-ownership-file.png` (Ownership, new this
  pass): the people leaderboard (bars relative to the top author, exact
  shares printed, bots counted), the hot files' owner mixes (stacked bars
  with the unshown long tail hatched), the single-owner watch list, and
  one file's ownership — owners, reviewer suggestions and recent commits
  for `src/flask/app.py`, the same file as
  `docs/demo/m4r-ownership-api.txt`. All values read live; section frames
  use a deterministic scroll on the console's main pane.
- `11-history.png` / `12-history-window.png` / `13-history-file.png` (Time
  machine, new this pass): the month skyline on live data, a window dragged
  open over the busiest month (read live from the API, never a constant),
  and the same view scoped to `src/flask/app.py` with a window of its own.
- `10-crew.png` (P1 Crew view): the newest recorded run's
  phase spine (planner → investigator → verifier), handoff notes queried
  back from `HANDOFF` edges, and the investigator's per-phase trace open.
- `08-memory.png` / `09-memory-asof.png` re-shot in the same pass; the
  as-of frame is still read live (one minute after the newest supersede) —
  no fabricated instant lands in docs/.
- Numbers inside the scripted answer text were read from `repo:flask` when
  the script was written; the graph keeps growing (memory writes), so live
  counts in these PNGs can drift from any later run. The console's own
  surfaces always read live. (Re-running the ask scenes records new
  episodes into `repo:flask` memory — by design; the memory stills show
  the ledger as it stood at capture time.)
- `hero-motion.gif` / `hero-motion.webm` were recorded earlier the same day
  (`scripts/record_motion.py`); the motion system is unchanged since.
- The demo video (`docs/demo/archivist-demo.mp4`, recorded 2026-10-16 ~21:14Z)
  runs its ask scenes on this scripted server — scenario order in
  `ask-script.json` is url_for trace → memory recall (`tag.py`) → wsgi_app;
  recording/assembly is `scripts/record_demo.py` + `scripts/assemble_demo.py`.
- Re-shoot recipe (waits on the canvas `data-settled` contract; fails on
  any JS error):

  ```
  .venv/bin/python scripts/serve.py --http-port 8787          # live
  .venv/bin/python scripts/serve.py --http-port 8789 \
      --llm-script docs/demo/m4r/ask-script.json              # scripted
  .venv/bin/python scripts/capture_console_v2.py --scene all \
      --url http://127.0.0.1:8787 --ask-url http://127.0.0.1:8789 \
      --out docs/demo/m4r
  ```
