#!/usr/bin/env python3
"""Record the Console v2 hero's ambient motion as a .webm (M4R motion evidence).

Usage (from the repo root, live console serving on :8787):

    .venv/bin/python scripts/record_motion.py --sanity-shot /tmp/hero-sanity.png

Launches headless Chromium via Playwright with `record_video_dir`, waits for
the landing entrance to be staged (window.__archivist.stagedAt — same timeline
token as capture_console_v2.py's wait_stage), then lets the hero's signal
pulses travel for a few seconds and closes the context to flush the .webm
(sync API only flushes video on context close). Prints the produced path as
the last stdout line; fails loudly (nonzero exit) on any page error so a
broken build never lands in docs/.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

# Machines without root can keep Chromium's system libs in a local prefix
# (same fix as capture_console_v2.py / capture_web.py — see INFRA-NOTES).
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


def wait_stage(page: Page, ms: float) -> None:
    """Wait until the landing entrance has been staged for >= `ms`."""
    page.wait_for_function(
        "ms => window.__archivist && window.__archivist.stagedAt > 0 && "
        "performance.now() - window.__archivist.stagedAt >= ms",
        arg=ms,
        timeout=20000,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="http://127.0.0.1:8787", help="live console")
    ap.add_argument(
        "--staged-before",
        type=float,
        default=1200,
        help="ms the entrance must have been staged before recording the motion",
    )
    ap.add_argument(
        "--seconds",
        type=float,
        default=6.0,
        help="seconds of hero motion to record once staged",
    )
    ap.add_argument(
        "--sanity-shot",
        default=None,
        help="optional path for one sanity screenshot (PNG) taken at the end",
    )
    args = ap.parse_args()

    base = args.url.rstrip("/")
    video_dir = Path(tempfile.mkdtemp(prefix="archivist-motion-"))

    errors: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, env=browser_env())
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            device_scale_factor=1,
            record_video_dir=str(video_dir),
            record_video_size={"width": 1280, "height": 800},
        )
        page = context.new_page()
        page.set_default_timeout(25000)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(f"{base}/console/", wait_until="load")
        wait_stage(page, args.staged_before)
        page.wait_for_timeout(int(args.seconds * 1000))

        if args.sanity_shot:
            page.screenshot(path=str(args.sanity_shot))
            print(f"sanity shot: {args.sanity_shot}")

        context.close()  # flushes the .webm
        browser.close()

    vids = sorted(video_dir.glob("*.webm"))
    if errors:
        print("page errors:", errors, file=sys.stderr)
        return 2
    if not vids:
        print(f"no .webm produced in {video_dir}", file=sys.stderr)
        return 3
    print(f"webm: {vids[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
