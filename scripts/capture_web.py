#!/usr/bin/env python3
"""Capture console screenshots for demos and the README (M4).

Usage:
    .venv/bin/python scripts/capture_web.py --url http://127.0.0.1:8787 --out docs/demo/m4b

Requires the dev extras (Playwright) and the console server running:
    pip install -r requirements-dev.txt
    python -m playwright install chromium
    python scripts/serve.py            # in another shell

Views:
    01-overview          landed console: hubs, memory, canvas hint
    02-symbol-url_for    search -> open `url_for` (src/flask/helpers.py) call graph
    03-file-helpers      breadcrumb into the file view (symbols + history)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

# Machines without root can keep Chromium's system libs in a local prefix and
# point us at them (e.g. debs unpacked under ~/.local/opt/pw-deps/root/).
_LOCAL_LIBS = Path.home() / ".local/opt/pw-deps/root/usr/lib/x86_64-linux-gnu"


def browser_env() -> dict[str, str]:
    env = dict(os.environ)
    libs = os.environ.get("ARCHIVIST_PW_LIBS") or (str(_LOCAL_LIBS) if _LOCAL_LIBS.is_dir() else "")
    if libs:
        env["LD_LIBRARY_PATH"] = libs + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
    return env


def settle(page: Page, ms: int = 2600) -> None:
    """Give the cose layout + async fetches time to finish before the shot."""
    page.wait_for_timeout(ms)


def shot(page: Page, out: Path, name: str) -> None:
    path = out / f"{name}.png"
    page.screenshot(path=str(path))
    print(f"  wrote {path} ({path.stat().st_size // 1024} KiB)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="http://127.0.0.1:8787", help="console base URL")
    ap.add_argument("--out", default="docs/demo/m4b", help="output directory for PNGs")
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--scale", type=float, default=2.0, help="device scale factor")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, env=browser_env())
        page = browser.new_page(
            viewport={"width": args.width, "height": args.height},
            device_scale_factor=args.scale,
        )
        page.set_default_timeout(20000)

        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # 01 — landing: the console has booted once the hub list is populated
        page.goto(args.url, wait_until="load")
        page.wait_for_selector("#hub-symbols button")
        settle(page)
        shot(page, out, "01-overview")

        # 02 — symbol: search `url_for` and open the helpers.py hit
        page.fill("#search-input", "url_for")
        page.press("#search-input", "Enter")
        page.wait_for_selector("#search-results button")
        hits = page.locator("#search-results button")
        target = hits.filter(has_text="helpers.py").first
        (target if target.count() else hits.first).click()
        page.wait_for_function(
            "document.getElementById('stage-stats').textContent.includes('nodes')"
        )
        settle(page)
        shot(page, out, "02-symbol-url_for")

        # 03 — file: breadcrumb into the focused file (symbols + recent history)
        page.locator("#crumbs button").nth(1).click()
        page.wait_for_selector(".file-meta", state="visible")
        settle(page)
        shot(page, out, "03-file-helpers")

        browser.close()

        if errors:
            print("page errors:", errors, file=sys.stderr)
            return 2

    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
