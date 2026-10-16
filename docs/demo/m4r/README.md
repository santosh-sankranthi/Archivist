# Console v2 demo captures (M4R)

Re-shot **2026-10-16 ~19:13Z** against the post-gate build (console at the
"M4R step 4 adoption" commits; served at `/console/`; ask scenes ran
against the scripted capture server `:8789` — real loop, tools and graph,
only the model's next-move choices are canned). `07-about` was refreshed
~19:33Z when the diagram's index annotation was rebound to its source.

Notes:

- Numbers inside the scripted answer text were read from `repo:flask` when
  the script was written; the graph keeps growing (memory writes), so live
  counts in these PNGs can drift from any later run. The console's own
  surfaces always read live.
- `hero-motion.gif` / `hero-motion.webm` were recorded earlier the same day
  (`scripts/record_motion.py`); the motion system is unchanged since.
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
