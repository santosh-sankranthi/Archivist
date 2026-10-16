# Taste spike 01 — "Signal Field" hero · Archivist landing concept

**Status:** visual direction spike for DESIGN-BRIEF §P2.1 (TASTE ESCALATION). Not wired into the
React app. Offline, self-contained, zero network requests. All edits live in this folder only.

## Files

| File | What it is |
|---|---|
| `hero.html` | **Built artifact** — single self-contained page (209 KiB). Open it directly; fonts are base64-embedded woff2 (latin subsets from `@fontsource`). |
| `hero.template.html` | Source of truth (fonts as `__FONT_*__` placeholders). Edit this, then rebuild. |
| `build.py` | Embeds the six woff2 faces (Instrument Serif 400/400i, Inter 400/500, JetBrains Mono 400/500) → `hero.html`. |
| `capture.py` | Playwright shots, 1440×900 @2x, `file://`, mirrors `scripts/capture_web.py` Chromium setup (`ARCHIVIST_PW_LIBS` prefix handling). |
| `measure.py` | QA tool: element bounding boxes, 4× detail crops, CTA hover state, reduced-motion frame. |
| `shot-hero.png` | **Settled state** ≈1.5 s after entrance start (2880×1800 = 1440×900 @2x). |
| `shot-hero-b.png` | **Energy state** ≈2.8 s — signals mid-flight, trails + node flashes live. |
| `shot-reduced.png` | Reduced-motion static frame (evidence for `prefers-reduced-motion` parity). |

Rebuild + recapture:

```bash
cd /home/openclaw/projects/archivist
.venv/bin/python docs/spikes/taste-01/build.py
.venv/bin/python docs/spikes/taste-01/capture.py     # the two deliverable shots
.venv/bin/python docs/spikes/taste-01/measure.py     # QA crops + states (crops are throwaway)
```

## Direction (5 lines)

Precision-instrument × editorial for engineers and hackathon judges: a dark "ink" field where the
product thesis is literally visible — a living graph. Left-aligned editorial column (serif display,
mono data) instead of a centered SaaS template; the right two-thirds breathe as a drifting
constellation wearing real flask symbols as whispers. One ember accent, disciplined to hero-signal
moments: the drawn rule under *"path"*, the primary CTA, and the traveling signal pulses.
The wordmark lockup ("ARCHIVIST" + mono kicker) sits like a plate on an instrument. Everything else
is hairlines, air, and grain.

**Copy deck:** kicker `FALKORDB-NATIVE ENGINEERING CONSOLE` · H1 `Every answer has a path.`
(*path* italic + drawn ember rule + focus-node dot) · sub `Ask your codebase anything — every
answer arrives wired to the code that proves it.` · CTAs `Enter the console` (primary, ember
gradient) / `See a trace` (ghost) · stats band `INDEX — REPO:FLASK` + `QUERIED FROM FALKORDB — NOT
ESTIMATED` · footer `© 2026 ARCHIVIST — RUNS OFFLINE` + `CONSOLE · ARCHITECTURE · JUDGE BRIEF`.

## Palette + type

- Base `#07080B` (P2 override); text `#ECEEF4` / `#A8B0C0`; micro-labels `#8A93A8` (**6.5:1** on
  base — verified). Hairlines `rgba(236,238,244,.07–.14)`.
- ONE accent: ember gradient `#F0A94E → #E06B3C` (CTA, drawn rule, signal pulses only). Soft amber
  glows restricted to signal moments (pulse heads, node flashes, underline dot). No purple/indigo,
  no glass, no emoji. Button text `#170F06` on ember = **9.5:1**.
- Type: Instrument Serif 400 (+italic — the *"path"* moment; only serif on screen besides the
  wordmark and stat-adjacent moments), Inter 400/500 (sub, CTA), JetBrains Mono 400/500 (kicker,
  meta, stat numbers, footer). Display size `clamp(3.5rem, 4.95vw, 4.5rem)` → 71.3 px @1440.
- Materials: grain ~2.5% (SVG turbulence tile), soft vignette, left "text-protect" gradient,
  ember field wash, hairline stat band + footer strip.

## Motion inventory

**Staged entrance (≤1.2 s total, ease-out-quint `cubic-bezier(.23,1,.32,1)` unless noted):**

| t (ms) | Element | Animation |
|---|---|---|
| 0 | Wordmark lockup | rise+fade 420 ms |
| 40 | Kicker + top-right meta | rise+fade 420 ms |
| 120 → 216 | H1 words (24 ms stagger) | rise 480 ms each → done ~0.70 s |
| 460 | Sub-line | rise 440 ms |
| 560 | CTA pair | rise 440 ms |
| 600 / 640 | Stat-band hairline `scaleX(0→1)` 480 ms / rows rise 420 ms (44 ms stagger) | done ~1.05 s |
| 640 | Ember rule under *path* draws (dashoffset, `cubic-bezier(.22,.9,.24,1)` 480 ms) | done 1.12 s |
| 640 → 1192 | Stat count-ups 420 ms each (easeOutCubic, 44 ms stagger) | ends ~1.19 s |
| 800 | Footer strip fade | done 1.12 s |
| 1000 | Focus-node dot pops in; ring breathing starts | ambient |
| 0–1200 | Canvas opacity 0→1 (1.2 s ease) | done 1.2 s |

**Continuous ambient (after entrance):**

1. **Drift** — ~113 nodes (area-scaled 70–120) in 3 depth layers; per-node orbit ±5–14 px/22–32 s +
   ±1.5–4 px/7–12 s; whole field rotates 360°/7 min. Pointer parallax lerped at 2.2/s, ±11 px
   scaled by depth.
2. **Signals** — spawn 1.2–2/s (first at 1.35 s), max 4 airborne; travel 180–270 px/s along 3–6
   consecutive edges; additive glow head (52 px sprite), trail ≈ 44 points (~0.7 s) with
   `pow(u,2)×0.58` falloff; traversed edges light amber then decay `exp(−1.15·dt/s)`; arrival
   flashes the node halo, decay `exp(−1.5·dt/s)`; finished pulses fade out over 0.9 s.
3. **Breathing** — node radii ±6 % @ ~6.5 s period; underline dot wears a 4.2 s expanding ring.
4. **Hover/press** — primaries lift −1 px + glow growth on hover (180 ms ease), press scale .985
   (120–140 ms), CTA arrow nudges +3 px.
5. **Reduced motion** — no rAF loop; one static frame (nodes, edges, 3 frozen signals mid-path,
   labels, flashes lit); all entrance states final; count-ups instant.

## What to port into the React app

- **Tokens**: add `--ember-a/--ember-b` + the landing-only base `#07080B` to `tokens.css`; keep
  hairlines/labels/semantics from the existing system.
- **HeroCanvas component**: port the engine as a module (seeded layout, 3 depth layers, pulse
  scheduler, sprite glow, DPR cap 2, visibility pause). Expose `window.__archivist` timeline token
  in dev only — `capture.py` uses it for deterministic shots.
- **EntranceCascade**: keep the `--d` CSS custom-property delay pattern; gate on
  `document.fonts.ready` (180 ms fallback) so text never swaps mid-animation.
- **StatStrip**: `data-count` + one shared rAF count-up (easeOutCubic, 420 ms, 44 ms stagger);
  wire the four numbers to `/api/state` at render time (spike numbers are hardcoded).
- **Materials**: grain tile + vignette + protect as fixed divs (z-order is documented in the
  template's section comments); reduce `mix-blend` risk by keeping them `pointer-events:none`.
- **Route**: `/console/` = this hero; nav CTA "Enter the console" → `/console/overview`. Keep below
  the fold for the P2.1 capability cards (Trace/Memory/Multi-agent).
- Fonts in the app come from `@fontsource` imports (already offline); the data-URL embedding here
  exists only so this spike is a single file.

## QA log (loop performed)

1. Rendered → inspected full shots + 4× crops → found **ember rule not drawing** (SVG gradient on a
   zero-height `<line>` bbox fails; fixed with `gradientUnits="userSpaceOnUse"`).
2. Tuned: node/edge presence (+10 %), label alpha, ember field wash, trail falloff `pow 2.4→2.0`,
   first pulse 1.55→1.35 s, pulse density.
3. Two rounds of re-shoot; final pass adds no-JS graceful degradation (underline + numbers render
   static; dead clock hidden). Capture runs: **zero console/page errors**.

## Honest caveats

- The graph is *ambient*, not data-driven: fixed seed, representative flask symbol labels
  (`url_for()`, `Blueprint`, …), random-walk pulses. For the real site, feed a real subgraph from
  the RO endpoints if you want the backdrop to be defensibly "the repo".
- Composition is tuned for ≥1280 px wide (degrades at smaller sizes but not pixel-obsessed).
- 60 fps target holds in headless Chromium with DPR≤2; if a weak GPU stutters, drop node count or
  the second (wide) trail pass first.
- Count-up numbers are hardcoded in markup/`data-count` (spike); they must be read from the API in
  the app. The UTC clock is live but cosmetic.
- Pulse spawns are seeded-deterministic at the current seed; changing viewport size rebuilds the
  field (same seed) — shots at other sizes will differ in node placement but keep character.
