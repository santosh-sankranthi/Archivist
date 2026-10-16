# Console v2 demo captures (M4R)

Re-shot **2026-10-16 ~19:13Z** against the post-gate build (console at the
"M4R step 4 adoption" commits; served at `/console/`; ask scenes ran
against the scripted capture server `:8789` — real loop, tools and graph,
only the model's next-move choices are canned). `07-about` was refreshed
~19:33Z when the diagram's index annotation was rebound to its source.

Notes:

- `08-memory.png` / `09-memory-asof.png` (M4R P1 Memory view, added
  ~22:35Z, re-shot 23:17Z) ran on the live server `:8787`; the as-of frame is the same
  ledger with the scrubber driven to one minute after the newest
  supersede — the target instant is read from the API at capture time, so
  no fabricated moment ever lands in docs/.
- Numbers inside the scripted answer text were read from `repo:flask` when
  the script was written; the graph keeps growing (memory writes), so live
  counts in these PNGs can drift from any later run. The console's own
  surfaces always read live.
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
