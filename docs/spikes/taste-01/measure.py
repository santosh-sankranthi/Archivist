#!/usr/bin/env python3
"""QA tool for hero.html — geometry, detail crops, hover + reduced-motion states.

- Prints bounding boxes of key elements (verifies layout numerically).
- Zooms into regions at device_scale_factor=4 (crop-*.png).
- Captures the primary-CTA hover state (crop-cta-hover.png).
- Captures a full frame with prefers-reduced-motion (shot-reduced.png).

Usage:  /home/openclaw/projects/archivist/.venv/bin/python \
            docs/spikes/taste-01/measure.py
"""

from __future__ import annotations

import json
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


SELECTORS = [
    ".lockup", ".topbar-meta", ".overline", ".display", ".w-path", ".u",
    ".sub", ".cta-row", ".btn-primary", ".btn-ghost", ".rig", ".rig-rule",
    ".rig-head", ".rig-stats", ".stat:last-child .stat-num", ".foot",
]


def main() -> int:
    url = (HERE / "hero.html").absolute().as_uri()
    errors: list[str] = []
    bad_logs: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, env=browser_env())

        # --- geometry + detail crops at 4x ---
        page = browser.new_page(
            viewport={"width": 1440, "height": 900}, device_scale_factor=4.0
        )
        page.set_default_timeout(20000)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on(
            "console",
            lambda m: bad_logs.append(f"{m.type}: {m.text}")
            if m.type in ("error", "warning")
            else None,
        )
        page.goto(url, wait_until="load")
        page.wait_for_function(
            "window.__archivist && window.__archivist.stagedAt && "
            "performance.now() - window.__archivist.stagedAt >= 1500"
        )

        boxes = {}
        for sel in SELECTORS:
            el = page.query_selector(sel)
            boxes[sel] = el.bounding_box() if el else None
        print(json.dumps(boxes, indent=1))

        def crop(name: str, sel: str, pad: float = 10.0):
            b = boxes.get(sel)
            if not b:
                print("skip", sel)
                return
            x = max(0, b["x"] - pad)
            y = max(0, b["y"] - pad)
            w = min(1440 - x, b["width"] + 2 * pad)
            h = min(900 - y, b["height"] + 2 * pad)
            page.screenshot(
                path=str(HERE / name), clip={"x": x, "y": y, "width": w, "height": h}
            )
            print("wrote", name, [round(v) for v in (x, y, w, h)])

        crop("crop-headline.png", ".display", 24)
        crop("crop-path.png", ".w-path", 16)
        crop("crop-cta.png", ".cta-row", 14)
        crop("crop-stats.png", ".rig", 16)
        crop("crop-topbar.png", ".lockup", 12)

        page.hover(".btn-primary")
        page.wait_for_timeout(260)
        page.screenshot(
            path=str(HERE / "crop-cta-hover.png"),
            clip={"x": 42, "y": 451, "width": 280, "height": 78},
        )
        print("wrote crop-cta-hover.png")
        page.close()

        # --- reduced motion full frame at 2x ---
        page2 = browser.new_page(
            viewport={"width": 1440, "height": 900}, device_scale_factor=2.0
        )
        page2.on("pageerror", lambda e: errors.append(str(e)))
        page2.emulate_media(reduced_motion="reduce")
        page2.goto(url, wait_until="load")
        page2.wait_for_timeout(1500)
        page2.screenshot(path=str(HERE / "shot-reduced.png"))
        print("wrote shot-reduced.png")
        page2.close()

        browser.close()

    for l in bad_logs:
        print("console:", l, file=sys.stderr)
    for e in errors:
        print("pageerror:", e, file=sys.stderr)
    print("done — ERRORS" if (bad_logs or errors) else "done — clean")
    return 2 if (bad_logs or errors) else 0


if __name__ == "__main__":
    raise SystemExit(main())
