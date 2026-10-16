#!/usr/bin/env python3
"""Capture taste-01 hero screenshots (1440x900 @2x, dark, file://).

Mirrors scripts/capture_web.py's Chromium setup, including the no-root
system-libs prefix handling (ARCHIVIST_PW_LIBS, default
~/.local/opt/pw-deps/root/usr/lib/x86_64-linux-gnu).

Shots are pinned to the page's own animation timeline (window.__archivist):
  shot-hero.png    — settled state  (~1.5s after entrance starts)
  shot-hero-b.png  — energy state   (~2.8s; pulses mid-flight)

Usage:  /home/openclaw/projects/archivist/.venv/bin/python \
            docs/spikes/taste-01/capture.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

_LOCAL_LIBS = Path.home() / ".local/opt/pw-deps/root/usr/lib/x86_64-linux-gnu"
HERE = Path(__file__).parent


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


def wait_t(page, ms: int) -> None:
    page.wait_for_function(
        "window.__archivist && window.__archivist.stagedAt && "
        f"performance.now() - window.__archivist.stagedAt >= {ms}"
    )


def main() -> int:
    url = (HERE / "hero.html").absolute().as_uri()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, env=browser_env())
        page = browser.new_page(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=2.0,
        )
        page.set_default_timeout(20000)

        errors: list[str] = []
        logs: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: logs.append(f"{m.type}: {m.text}"))

        page.goto(url, wait_until="load")

        wait_t(page, 1500)
        page.screenshot(path=str(HERE / "shot-hero.png"))
        print("wrote shot-hero.png")

        wait_t(page, 2800)
        page.screenshot(path=str(HERE / "shot-hero-b.png"))
        print("wrote shot-hero-b.png")

        browser.close()

        bad = [l for l in logs if l.startswith(("error", "warning"))]
        for l in bad:
            print("console:", l, file=sys.stderr)
        for e in errors:
            print("pageerror:", e, file=sys.stderr)

    print("done — ERRORS PRESENT" if (bad or errors) else "done — clean")
    return 2 if (bad or errors) else 0


if __name__ == "__main__":
    raise SystemExit(main())
