#!/usr/bin/env python3
"""Record the Graph Hacks demo video — one continuous take, headless.

Usage (two servers; the scripted one must be freshly started so scenarios
are claimed in order — restart `archivist-capture` immediately before a take):

    systemctl --user restart archivist-capture        # :8789, ask-script.json
    .venv/bin/python scripts/record_demo.py \
        --raw docs/demo/.raw/archivist-demo-raw.webm \
        --timeline docs/demo/.raw/archivist-demo-timeline.json

Drives the full shot list (hackathon/VIDEO-PLAN.md) in one Chromium session
with `record_video_dir`; logs every scene/action timestamp to a JSON
timeline so `scripts/assemble_demo.py` can burn captions without guessing.
Fails loudly on any page error so a broken take never proceeds to assembly.

The ask scenes run against the scripted capture server — the agent loop,
tools and graph are real; only the model's next moves are canned (and the
UI's status chip says "scripted (demo)" while it is). Everything on screen
during those scenes is produced live from `repo:flask`.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from string import Template

from playwright.sync_api import Page, sync_playwright

# Machines without root can keep Chromium's system libs in a local prefix
# (same trick as capture_console_v2.py — see INFRA-NOTES).
_LOCAL_LIBS = Path.home() / ".local/opt/pw-deps/root/usr/lib/x86_64-linux-gnu"

Q1 = "Who calls url_for — and what would break if its signature changed?"
Q2 = "Did we look into tag.py before? What did we conclude?"

# Canvas spots to try when selecting a node (CSS px @1600x1000 — the same
# probe list as capture_console_v2.py, which walks until a real symbol hit).
NODE_SPOTS = [
    (1123, 624),
    (904, 647),
    (1176, 600),
    (824, 232),
    (757, 377),
    (667, 511),
    (853, 539),
    (1040, 821),
    (571, 843),
    (1160, 797),
]

# Injected before any page script: a short-lived ring on every click target
# so clicks read on video (headless Chromium draws no cursor), and a dark
# backdrop from the first frame so route loads never flash white.
RING_SCRIPT = """
(() => {
  const paint = () => {
    const el = document.documentElement;
    if (!el) return false;
    el.style.background = '#07080B';
    el.style.colorScheme = 'dark';
    return true;
  };
  if (!paint()) setTimeout(paint, 0);
  try {
    addEventListener('pointerdown', (e) => {
      try {
        if (!document.body) return;
        const ring = document.createElement('div');
        const s = 30;
        ring.style.cssText = [
          'position:fixed', 'z-index:2147483647', 'pointer-events:none',
          `left:${e.clientX - s / 2}px`, `top:${e.clientY - s / 2}px`,
          `width:${s}px`, `height:${s}px`,
          'border:2px solid rgba(232,163,76,.95)', 'border-radius:50%',
          'opacity:.95', 'transform:scale(.55)',
          'transition:opacity .6s ease-out, transform .6s ease-out',
        ].join(';');
        document.body.appendChild(ring);
        requestAnimationFrame(() => {
          ring.style.opacity = '0';
          ring.style.transform = 'scale(1.8)';
        });
        setTimeout(() => ring.remove(), 800);
      } catch (err) { /* recording aid only — never break the page */ }
    }, true);
  } catch (err) { /* recording aid only — never break the page */ }
})();
"""


def browser_env() -> dict[str, str]:
    env = dict(os.environ)
    libs = os.environ.get("ARCHIVIST_PW_LIBS") or (
        str(_LOCAL_LIBS) if _LOCAL_LIBS.is_dir() else ""
    )
    if libs:
        env["LD_LIBRARY_PATH"] = libs + (
            ":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else ""
        )
    return env


class Take:
    """One recording take: page + timestamped event log."""

    def __init__(self, page: Page, t0: float):
        self.page = page
        self.t0 = t0
        self.events: list[dict] = []

    def mark(self, name: str, **extra: object) -> None:
        record = {"name": name, "t": round(time.monotonic() - self.t0, 3)}
        record.update(extra)
        self.events.append(record)
        print(f"  [{record['t']:7.2f}s] {name}", flush=True)

    def hold(self, ms: int) -> None:
        self.page.wait_for_timeout(ms)


def wait_settled(page: Page) -> None:
    """Wait on the canvas `data-settled` stamp (capture protocol v2)."""
    page.wait_for_function(
        "() => document.querySelector('[data-settled]')?.dataset.settled === '1'",
        timeout=20000,
    )
    page.wait_for_timeout(1250)


def wait_stage(page: Page, ms: float) -> None:
    """Wait until the landing entrance has been staged for >= `ms`."""
    page.wait_for_function(
        "ms => window.__archivist && window.__archivist.stagedAt > 0 && "
        "performance.now() - window.__archivist.stagedAt >= ms",
        arg=ms,
        timeout=20000,
    )


def scene_landing(take: Take, base: str) -> None:
    page = take.page
    page.goto(f"{base}/console/", wait_until="load")
    wait_stage(page, 1400)
    take.mark("landing.settled")
    take.hold(5800)
    take.hold(7000)
    take.mark("landing.end")


def scene_home(take: Take, base: str) -> None:
    page = take.page
    page.goto(f"{base}/console/overview", wait_until="load")
    page.get_by_text("url_for changes", exact=False).first.wait_for(timeout=20000)
    page.wait_for_timeout(900)
    take.mark("home.ready")
    take.hold(8500)
    page.get_by_text(
        "What breaks if flask.helpers.url_for changes?", exact=False
    ).first.hover()
    take.hold(2200)
    page.get_by_text(
        "Who should review a change to src/flask/app.py?", exact=False
    ).first.hover()
    take.hold(2200)
    take.hold(2500)
    take.mark("home.end")


def scene_explorer(take: Take, base: str) -> None:
    page = take.page
    take.mark("ex.start")
    page.locator("#rail-search").click()
    page.keyboard.type("url_for", delay=50)
    take.mark("ex.typed")
    result = (
        page.locator('[aria-live="polite"] button')
        .filter(has_text="url_for")
        .filter(has_text="helpers.py")
        .first
    )
    result.wait_for(timeout=15000)
    take.hold(1400)
    result.click()
    take.mark("ex.opened")
    wait_settled(page)
    take.mark("ex.settled")
    take.hold(5600)

    selected = False
    for x, y in NODE_SPOTS:
        page.mouse.click(x, y)
        page.wait_for_timeout(360)
        hit = (
            page.get_by_role("button", name="Find path from here…").count()
            or page.get_by_role("button", name="Focus call graph").count()
        )
        if hit:
            selected = True
            break
    take.mark("ex.selected", hit=selected)
    take.hold(5000)

    page.get_by_role("button", name="path finder").click()
    from_input = page.get_by_placeholder("From — symbol…")
    from_input.fill("finalize_request")
    take.hold(500)
    page.locator("button", has_text="finalize_request").filter(
        has_text="app.py"
    ).first.click()
    to_input = page.get_by_placeholder("To — symbol…")
    to_input.fill("wsgi_app")
    take.hold(500)
    page.locator("button", has_text="wsgi_app").filter(has_text="app.py").first.click()
    take.hold(600)
    page.get_by_role("button", name="trace path").click()
    take.mark("ex.trace")
    page.get_by_text("shortest path", exact=False).wait_for(timeout=15000)
    wait_settled(page)
    take.mark("ex.pathfound")
    take.hold(9500)
    take.mark("ex.end")


def ask_input(page: Page):
    return page.get_by_placeholder(re.compile("Ask the archive")).last


def scene_ask(take: Take, ask_base: str) -> None:
    page = take.page
    take.mark("ask.start")
    page.goto(f"{ask_base}/console/ask", wait_until="load")
    take.hold(2800)
    take.mark("ask.ready")
    box = ask_input(page)
    box.click()
    page.keyboard.type(Q1, delay=22)
    take.mark("ask.typed")
    take.hold(800)
    page.keyboard.press("Enter")
    take.mark("ask.submitted")
    page.get_by_text("25 callers", exact=False).first.wait_for(timeout=30000)
    wait_settled(page)
    take.mark("ask.answered")
    take.hold(13000)
    page.locator("[data-step='0']").first.click()
    page.locator('section[aria-label^="Step detail"]').wait_for(timeout=10000)
    page.wait_for_timeout(700)
    wait_settled(page)
    take.mark("ask.step1")
    take.hold(11500)
    page.locator("[data-step='1']").first.click()
    page.wait_for_timeout(900)
    wait_settled(page)
    take.mark("ask.step2")
    take.hold(11500)
    take.mark("ask.end")


def scene_memory(take: Take, ask_base: str) -> None:
    page = take.page
    take.mark("mem.start")
    take.hold(600)
    box = ask_input(page)
    box.click()
    page.keyboard.type(Q2, delay=22)
    take.mark("mem.typed")
    take.hold(800)
    page.keyboard.press("Enter")
    take.mark("mem.submitted")
    page.get_by_text("superseded", exact=False).first.wait_for(timeout=30000)
    take.mark("mem.answered")
    take.hold(7500)
    page.locator("[data-step='0']").last.click()
    page.locator('section[aria-label^="Step detail"]').wait_for(timeout=10000)
    page.wait_for_timeout(700)
    take.mark("mem.step")
    take.hold(10500)
    take.mark("mem.end")


def scene_about(take: Take, ask_base: str) -> None:
    page = take.page
    take.mark("about.start")
    page.goto(f"{ask_base}/console/about", wait_until="load")
    page.get_by_text("FalkorDB, load-bearing").wait_for(timeout=20000)
    take.mark("about.ready")
    take.hold(6200)
    page.mouse.wheel(0, 430)
    take.mark("about.scrolled")
    take.hold(6200)
    page.mouse.wheel(0, 430)
    take.hold(4600)
    take.mark("about.end")


def endcard_html(repo_root: Path) -> str:
    """The closing card, using the console's own bundled fonts (base64)."""

    def font(name: str) -> str:
        hits = sorted((repo_root / "console/dist/assets").glob(name))
        if not hits:
            raise SystemExit(f"endcard font missing: console/dist/assets/{name}")
        return base64.b64encode(hits[0].read_bytes()).decode()

    template = Template("""<!doctype html>
<html><head><meta charset="utf-8"><style>
@font-face{font-family:'Instrument Serif';font-weight:400;font-style:normal;
  src:url(data:font/woff2;base64,$serif) format('woff2');}
@font-face{font-family:'Inter';font-weight:400;font-style:normal;
  src:url(data:font/woff2;base64,$inter) format('woff2');}
@font-face{font-family:'JetBrains Mono';font-weight:400;font-style:normal;
  src:url(data:font/woff2;base64,$mono) format('woff2');}
html,body{margin:0;height:100%;background:#07080B;color:#EDE7DC;}
.wrap{height:100%;display:flex;flex-direction:column;align-items:center;
  justify-content:center;gap:16px;text-align:center;
  font-family:'Inter',sans-serif;-webkit-font-smoothing:antialiased;
  opacity:0;animation:rise .9s .15s ease-out forwards;}
@keyframes rise{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}
.kicker{font-family:'JetBrains Mono',monospace;font-size:13px;
  letter-spacing:.24em;color:#9a948a;text-transform:uppercase;}
h1{font-family:'Instrument Serif',serif;font-weight:400;font-size:88px;
  margin:6px 0 0;letter-spacing:.01em;}
.rule{width:132px;height:1px;margin:6px 0 2px;
  background:linear-gradient(90deg,transparent,#E8A34C,transparent);}
.sub{color:#B9B4A9;font-size:17px;}
.meta{margin-top:30px;font-family:'JetBrains Mono',monospace;font-size:14px;
  line-height:2;color:#CFC9BD;}
.meta .dim{color:#8F8A80;}
</style></head><body><div class="wrap">
  <div class="kicker">Graph Hacks &middot; Oct 15&ndash;18, 2026</div>
  <h1>Archivist</h1>
  <div class="rule"></div>
  <div class="sub">Engineering intelligence, mapped.</div>
  <div class="meta">
    github.com/santosh-sankranthi/Archivist<br>
    <span class="dim">live demo &middot; link in the README</span><br>
    <span class="dim">FalkorDB-native &middot; WeMakeDevs &times; FalkorDB</span>
  </div>
</div></body></html>""")
    return template.substitute(
        serif=font("instrument-serif-latin-400-normal-*.woff2"),
        inter=font("inter-latin-400-normal-*.woff2"),
        mono=font("jetbrains-mono-latin-400-normal-*.woff2"),
    )


def scene_endcard(take: Take, repo_root: Path) -> None:
    take.mark("endcard.start")
    take.page.set_content(endcard_html(repo_root), wait_until="load")
    take.hold(900)
    take.mark("endcard.shown")
    take.hold(8200)
    take.mark("endcard.end")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="http://127.0.0.1:8787", help="live console")
    ap.add_argument(
        "--ask-url", default="http://127.0.0.1:8789", help="scripted server"
    )
    ap.add_argument("--raw", default="docs/demo/.raw/archivist-demo-raw.webm")
    ap.add_argument("--timeline", default="docs/demo/.raw/archivist-demo-timeline.json")
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=1000)
    args = ap.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    raw_path = (repo_root / args.raw).resolve()
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    for stale in raw_path.parent.glob("*.webm"):
        stale.unlink()
    base = args.url.rstrip("/")
    ask_base = args.ask_url.rstrip("/")

    errors: list[str] = []
    video_dir = raw_path.parent

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, env=browser_env())
        context = browser.new_context(
            viewport={"width": args.width, "height": args.height},
            device_scale_factor=1,
            record_video_dir=str(video_dir),
            record_video_size={"width": args.width, "height": args.height},
        )
        t0 = time.monotonic()
        t0_wall = time.time()
        context.add_init_script(RING_SCRIPT)
        page = context.new_page()
        page.set_default_timeout(25000)
        page.on("pageerror", lambda e: errors.append(str(e)))

        take = Take(page, t0)
        take.mark("record.start")
        scene_landing(take, base)
        scene_home(take, base)
        scene_explorer(take, base)
        scene_ask(take, ask_base)
        scene_memory(take, ask_base)
        scene_about(take, ask_base)
        scene_endcard(take, repo_root)

        close = time.monotonic() - t0
        context.close()  # flushes the .webm (sync API flushes on close)
        browser.close()

    vids = sorted(video_dir.glob("*.webm"))
    if vids and vids[0].name != raw_path.name:
        if raw_path.exists():
            raw_path.unlink()
        vids[0].replace(raw_path)
        vids = [raw_path]
    timeline = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "t0_wall": datetime.fromtimestamp(t0_wall, tz=UTC).isoformat(
            timespec="seconds"
        ),
        "close": round(close, 3),
        "raw": str(vids[0]) if vids else None,
        "url": base,
        "ask_url": ask_base,
        "events": take.events,
    }
    tl_path = (repo_root / args.timeline).resolve()
    tl_path.write_text(json.dumps(timeline, indent=1) + "\n", encoding="utf-8")

    if errors:
        print("page errors:", errors, file=sys.stderr)
        return 2
    if not vids:
        print(f"no .webm produced in {video_dir}", file=sys.stderr)
        return 3
    print(f"take: {close:.1f}s wall, {len(take.events)} events")
    print(f"raw: {vids[0]}")
    print(f"timeline: {tl_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
