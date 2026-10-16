#!/usr/bin/env python3
"""Capture Console v2 (M4R) screenshots — Explorer pass.

Usage:
    .venv/bin/python scripts/capture_console_v2.py \
        --url http://127.0.0.1:8787 --out docs/demo/m4r

Drives the /console/ app over the live graph: the url_for neighbourhood
(settled canvas), a selection with the spotlight + inspector open, and a
path-finder result. Waits on the canvas `data-settled="1"` stamp instead
of fixed sleeps wherever the layout is the slow part; fails loudly on any
JS error so a broken build never lands in docs/.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

# Machines without root can keep Chromium's system libs in a local prefix
# (same trick as capture_web.py — see INFRA-NOTES).
_LOCAL_LIBS = Path.home() / ".local/opt/pw-deps/root/usr/lib/x86_64-linux-gnu"


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
        timeout=15000,
    )
    page.wait_for_timeout(650)  # let spotlight/edge transitions finish


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="http://127.0.0.1:8787")
    ap.add_argument("--out", default="docs/demo/m4r")
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=1000)
    ap.add_argument("--scale", type=float, default=2.0)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    base = args.url.rstrip("/")
    explorer = f"{base}/console/explorer?file=src/flask/helpers.py&qualname=url_for"

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, env=browser_env())
        page = browser.new_page(
            viewport={"width": args.width, "height": args.height},
            device_scale_factor=args.scale,
        )
        page.set_default_timeout(25000)

        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

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
        page.locator("button", has_text="wsgi_app").filter(
            has_text="app.py"
        ).first.click()

        page.get_by_role("button", name="trace path").click()
        page.get_by_text("shortest path", exact=False).wait_for()
        wait_settled(page)
        shot(page, out, "03-explorer-path")

        browser.close()

        if errors:
            print("page errors:", errors, file=sys.stderr)
            return 2

    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
