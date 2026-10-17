#!/usr/bin/env python3
"""Capture Console v2 (M4R) screenshots — Explorer + Ask/Trace passes.

Usage (two servers: the live console on :8787 for explorer scenes, the
scripted capture server for ask scenes — see docs/demo/m4r/ask-script.json):

    .venv/bin/python scripts/serve.py --http-port 8787          # live
    .venv/bin/python scripts/serve.py --http-port 8789 \
        --llm-script docs/demo/m4r/ask-script.json              # scripted

    .venv/bin/python scripts/capture_console_v2.py --scene all \
        --url http://127.0.0.1:8787 --ask-url http://127.0.0.1:8789 \
        --out docs/demo/m4r

Scenes: landing (hero, hero-energy), home (00), explorer (01–03), ask
(04–05), palette (06), about (07), memory (08–09), crew (10). Waits on the
canvas `data-settled="1"` stamp, on scripted answer text, and on the
landing's `window.__archivist.stagedAt` timeline token instead of fixed
sleeps wherever possible; fails loudly on any JS error so a broken build
never lands in docs/.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import Page, sync_playwright

# Machines without root can keep Chromium's system libs in a local prefix
# (same trick as capture_web.py — see INFRA-NOTES).
_LOCAL_LIBS = Path.home() / ".local/opt/pw-deps/root/usr/lib/x86_64-linux-gnu"

Q1 = "Who calls url_for — and what would break if its signature changed?"


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


def shot(page: Page, out: Path, name: str) -> None:
    path = out / f"{name}.png"
    page.screenshot(path=str(path))
    print(f"  wrote {path} ({path.stat().st_size // 1024} KiB)")


def wait_settled(page: Page) -> None:
    page.wait_for_function(
        "() => document.querySelector('[data-settled]')?.dataset.settled === '1'",
        timeout=20000,
    )
    # Let the spotlight (300ms), draw sweep + pulse handoff, and replay
    # fit/ripple (320ms glide + 680ms ring) land before the shot.
    page.wait_for_timeout(1250)


def wait_stage(page: Page, ms: float) -> None:
    """Wait until the landing entrance has been staged for >= `ms`."""
    page.wait_for_function(
        "ms => window.__archivist && window.__archivist.stagedAt > 0 && "
        "performance.now() - window.__archivist.stagedAt >= ms",
        arg=ms,
        timeout=20000,
    )


def run_landing(page: Page, base: str, out: Path) -> None:
    """hero / hero-energy: the landing at /console/ (entrance-timeline aligned)."""
    page.goto(f"{base}/console/", wait_until="load")
    wait_stage(page, 1500)
    shot(page, out, "hero")
    wait_stage(page, 2800)
    shot(page, out, "hero-energy")


def run_home(page: Page, base: str, out: Path) -> None:
    """00: overview — hero ask line, live stat strip, hubs, starters."""
    page.goto(f"{base}/console/overview", wait_until="load")
    page.get_by_text("url_for changes", exact=False).first.wait_for(timeout=20000)
    page.wait_for_timeout(700)
    shot(page, out, "00-home")


def run_explorer(page: Page, base: str, out: Path) -> None:
    """01–03: call neighbourhood, selection + inspector, path finder."""
    explorer = f"{base}/console/explorer?file=src/flask/helpers.py&qualname=url_for"

    # 01 — explorer: the url_for call neighbourhood, canvas settled.
    page.goto(explorer, wait_until="load")
    wait_settled(page)
    shot(page, out, "01-explorer-symbol")

    # 02 — selection: scan likely node/label spots until a non-focus
    # symbol takes (inspector grows "Focus call graph" / "Find path…").
    selected = False
    for x, y in (
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
        (1048, 885),
        (690, 895),
        (848, 924),
    ):
        page.mouse.click(x, y)
        page.wait_for_timeout(320)
        hit = (
            page.get_by_role("button", name="Find path from here…").count()
            or page.get_by_role("button", name="Focus call graph").count()
        )
        if hit:
            selected = True
            break
    if selected:
        page.wait_for_timeout(500)
        shot(page, out, "02-explorer-selection")
    else:
        print("  note: no node click landed — selection shot skipped")

    # 03 — path finder: reverse-direction trace (finalize_request is the
    # caller-end here; the server answers "runs to → from" honestly).
    page.goto(explorer, wait_until="load")
    wait_settled(page)
    page.get_by_role("button", name="path finder").click()

    from_input = page.get_by_placeholder("From — symbol…")
    from_input.fill("finalize_request")
    page.locator("button", has_text="finalize_request").filter(
        has_text="app.py"
    ).first.click()

    to_input = page.get_by_placeholder("To — symbol…")
    to_input.fill("wsgi_app")
    page.locator("button", has_text="wsgi_app").filter(has_text="app.py").first.click()

    page.get_by_role("button", name="trace path").click()
    page.get_by_text("shortest path", exact=False).wait_for()
    wait_settled(page)
    shot(page, out, "03-explorer-path")


def run_ask(page: Page, base: str, out: Path) -> None:
    """04–05: Trace Ribbon (scripted answer) + step focus with replay."""
    page.goto(f"{base}/console/ask?q={quote(Q1)}", wait_until="load")
    # The scripted final text is deterministic — wait for it, then settle.
    page.get_by_text("25 callers", exact=False).first.wait_for(timeout=30000)
    wait_settled(page)
    shot(page, out, "04-ask-ribbon")

    # 05 — chip click: spotlight the step's subgraph + open its detail.
    page.locator("[data-step='0']").click()
    page.locator('section[aria-label^="Step detail"]').wait_for(timeout=10000)
    page.wait_for_timeout(700)
    wait_settled(page)
    shot(page, out, "05-ask-step")


def run_palette(page: Page, base: str, out: Path) -> None:
    """06: ⌘K palette — live symbol results, plus a keyboard-drive check."""
    page.goto(f"{base}/console/overview", wait_until="load")
    page.get_by_text("url_for changes", exact=False).first.wait_for(timeout=20000)
    page.keyboard.press("Control+k")
    palette_input = page.get_by_role("combobox")
    palette_input.wait_for(timeout=10000)
    palette_input.fill("url_for")
    page.get_by_text("flask.helpers.url_for", exact=False).first.wait_for(timeout=20000)
    page.wait_for_timeout(700)  # spring settle after the results land
    shot(page, out, "06-palette")

    # Drive it: down to the second row, Enter — must land on the Explorer
    # drill-down for that symbol, and the palette must close behind us.
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    page.wait_for_timeout(400)
    url = page.url
    if "/explorer?" not in url or "qualname=" not in url:
        raise RuntimeError(f"palette keyboard drive failed — url is {url!r}")
    if page.get_by_role("dialog", name="Command palette").count():
        raise RuntimeError("palette stayed open after Enter")


def run_about(page: Page, base: str, out: Path) -> None:
    """07: About/judges — headline + pipeline diagram."""
    page.goto(f"{base}/console/about", wait_until="load")
    page.get_by_text("FalkorDB, load-bearing").wait_for(timeout=20000)
    page.wait_for_timeout(400)
    shot(page, out, "07-about")


def run_memory(page: Page, base: str, out: Path) -> None:
    """08–09: memory ledger — now frame, then an honest as-of frame.

    The as-of target is read live: one minute after the newest supersede,
    so the frame shows the claim that was true then (and the episodes it
    sits between), not a fabricated instant.
    """
    page.goto(f"{base}/console/memory", wait_until="load")
    page.wait_for_selector("[data-memory-mode='now']", timeout=20000)
    page.get_by_text("What the archive remembers.").wait_for(timeout=20000)
    page.wait_for_timeout(900)  # bar draw-in + fact stagger land
    shot(page, out, "08-memory")

    payload = page.request.get(f"{base}/api/graph/memory").json()
    moments = [
        f["valid_at"]
        for f in payload.get("facts", [])
        if f.get("supersedes") and f.get("valid_at")
    ]
    if not moments:
        print("  note: no supersede in the ledger — as-of shot skipped")
        return
    target = max(moments) + 60
    page.locator("input[type='range']").evaluate(
        "(el, target) => {"
        "  el.value = String(target);"
        "  el.dispatchEvent(new Event('input', { bubbles: true }));"
        "}",
        target,
    )
    page.wait_for_selector("[data-memory-mode='as_of']", timeout=20000)
    page.wait_for_timeout(900)
    shot(page, out, "09-memory-asof")


def run_crew(page: Page, base: str, out: Path) -> None:
    """10: crew — phase spine + handoff notes; one phase detail open.

    Runs on live data: the newest recorded crew run. Opens the second
    spine card (the investigator, in the recorded order) so the shot
    shows the spine, the graph-queried handoff notes and a per-phase
    trace at once.
    """
    page.goto(f"{base}/console/crew", wait_until="load")
    page.wait_for_selector("[data-run]", timeout=20000)
    page.wait_for_selector("[data-link]", timeout=20000)
    page.wait_for_timeout(800)  # card stagger + note clamp settle
    cards = page.locator("[data-phase]")
    count = cards.count()
    if count == 0:
        raise RuntimeError("crew view renders no phase cards")
    cards.nth(1 if count > 1 else 0).click()
    page.locator('section[aria-label^="Phase detail"]').wait_for(timeout=10000)
    page.wait_for_timeout(700)  # detail rise + chips land
    shot(page, out, "10-crew")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="http://127.0.0.1:8787", help="live console")
    ap.add_argument(
        "--ask-url",
        default=None,
        help="scripted capture server for ask scenes (default: --url)",
    )
    ap.add_argument(
        "--scene",
        choices=[
            "all",
            "landing",
            "home",
            "explorer",
            "ask",
            "palette",
            "about",
            "memory",
            "crew",
        ],
        default="all",
    )
    ap.add_argument("--out", default="docs/demo/m4r")
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=1000)
    ap.add_argument("--scale", type=float, default=2.0)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    base = args.url.rstrip("/")
    ask_base = (args.ask_url or args.url).rstrip("/")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, env=browser_env())
        page = browser.new_page(
            viewport={"width": args.width, "height": args.height},
            device_scale_factor=args.scale,
        )
        page.set_default_timeout(25000)

        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        if args.scene in ("all", "landing"):
            run_landing(page, base, out)
        if args.scene in ("all", "home"):
            run_home(page, base, out)
        if args.scene in ("all", "explorer"):
            run_explorer(page, base, out)
        if args.scene in ("all", "ask"):
            run_ask(page, ask_base, out)
        if args.scene in ("all", "palette"):
            run_palette(page, base, out)
        if args.scene in ("all", "about"):
            run_about(page, base, out)
        if args.scene in ("all", "memory"):
            run_memory(page, base, out)
        if args.scene in ("all", "crew"):
            run_crew(page, base, out)

        browser.close()

        if errors:
            print("page errors:", errors, file=sys.stderr)
            return 2

    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
