#!/usr/bin/env python3
"""Auth flow QA for the console (M6) — signup → account → logout → login.

Usage (scratch server with a throwaway data dir — never the live one):

    .venv/bin/python scripts/serve.py --http-port 8791 --data-dir /tmp/qa-accounts
    .venv/bin/python scripts/qa_auth_web.py --url http://127.0.0.1:8791 \
        --out docs/demo/m6b

Fails loudly (exit 2) on any console JS error so a broken build never reads
as a pass. Screenshots land in the output dir; every assertion is on text
the app actually rendered.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

_LOCAL_LIBS = Path.home() / ".local/opt/pw-deps/root/usr/lib/x86_64-linux-gnu"
USERNAME = "m6qauser"
PASSWORD = "correct horse battery"


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="http://127.0.0.1:8791")
    ap.add_argument("--out", default="docs/demo/m6b")
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--scale", type=float, default=2.0)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    base = args.url.rstrip("/")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, env=browser_env())
        page = browser.new_page(
            viewport={"width": args.width, "height": args.height},
            device_scale_factor=args.scale,
        )
        page.set_default_timeout(20000)
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # 1 — anonymous shell: the chip offers sign-in, the demo keeps working.
        # Scope the chip to the header — auth pages also carry a primary
        # "Sign in" button, and strict mode will not guess which one we mean.
        chip = page.locator("header").get_by_role("button", name="Sign in")
        page.goto(base + "/console/overview", wait_until="load")
        chip.wait_for()
        print("  ok: anonymous chip reads 'Sign in'")

        # 2 — signup form, filled, then submitted through the real API.
        page.goto(base + "/console/signup", wait_until="load")
        page.fill("input#field-username", USERNAME)
        page.fill("input#field-password", PASSWORD)
        page.fill("input#field-repeat-password", PASSWORD)
        shot(page, out, "01-signup")
        page.get_by_role("button", name="Create account").click()
        page.wait_for_url("**/console/account*")
        # The chip also reads @user, so assert on the card inside main —
        # then let the route rise/fade land before the still.
        page.get_by_role("main").get_by_role("heading", name=f"@{USERNAME}").wait_for()
        page.get_by_role("main").get_by_text("Member since").wait_for()
        page.wait_for_timeout(450)
        print("  ok: signup → /account shows the profile")
        shot(page, out, "02-account")

        # 3 — sign out from the account page; the chip returns to 'Sign in'.
        page.get_by_role("button", name="Sign out").click()
        page.wait_for_url("**/console/overview")
        page.locator("header").get_by_role("button", name="Sign in").wait_for()
        print("  ok: sign out clears the session")

        # 4 — log back in from /login; the chip now shows the handle.
        page.goto(base + "/console/login", wait_until="load")
        page.fill("input#field-username", USERNAME)
        page.fill("input#field-password", PASSWORD)
        page.get_by_role("main").get_by_role("button", name="Sign in").click()
        page.wait_for_url("**/console/account*")
        page.get_by_role("main").get_by_role("heading", name=f"@{USERNAME}").wait_for()
        page.wait_for_timeout(450)
        print("  ok: login → /account again")
        shot(page, out, "03-login-account")

        browser.close()

        if errors:
            print("page errors:", errors, file=sys.stderr)
            return 2

    print("done — auth flow green, zero JS errors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
