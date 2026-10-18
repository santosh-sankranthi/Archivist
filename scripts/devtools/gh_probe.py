#!/usr/bin/env python3
"""GitHub bootstrap for the community agent: token check, fork, labels, webhook.

Usage (on the box, from the repo root):
    .venv/bin/python scripts/devtools/gh_probe.py            # token + .env hints
    .venv/bin/python scripts/devtools/gh_probe.py --fork
    .venv/bin/python scripts/devtools/gh_probe.py --labels
    .venv/bin/python scripts/devtools/gh_probe.py --webhook

Reads ARCHIVIST_GH_* from the repo-root .env (real env wins). Never prints
the token. --webhook needs the server route deployed and ARCHIVIST_GH_URL
pointing at the tunnel.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env(ROOT / ".env")

from archivist.gh.client import GhError, GitHub

SRC = os.environ.get("ARCHIVIST_GH_FORK", "FalkorDB/falkordb-py")
LABELS = [("needs-info", "fbca04", "More information needed from the reporter")]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fork", action="store_true")
    parser.add_argument("--labels", action="store_true")
    parser.add_argument("--webhook", action="store_true")
    args = parser.parse_args()
    token = (os.environ.get("ARCHIVIST_GH_TOKEN") or "").strip()
    if not token:
        print("ARCHIVIST_GH_TOKEN not set (checked repo-root .env)")
        return 1
    gh = GitHub(token)
    login = gh.get_user().get("login") or "?"
    print(f"token ok — login: {login}")
    slug = (os.environ.get("ARCHIVIST_GH_SLUG") or "").strip()
    if not slug:
        slug = f"{login}/{SRC.split('/')[-1]}"
    print(f"target repo: {slug}")

    if args.fork:
        repo = gh.get_repo(slug)
        if repo:
            print(f"fork already exists: {repo['html_url']}")
        else:
            print(f"forking {SRC} ...")
            gh._request("POST", f"/repos/{SRC}/forks", body={})
            for _ in range(20):
                time.sleep(3)
                if gh.get_repo(slug):
                    print(f"fork created: https://github.com/{slug}")
                    break
            else:
                print("fork not ready yet — re-run --fork in a minute")
                return 1

    if args.labels:
        for name, color, desc in LABELS:
            code, _payload = gh._request(
                "POST",
                f"/repos/{slug}/labels",
                body={"name": name, "color": color, "description": desc},
                expect=(201, 422),
            )
            print(f"label {name}: {'exists' if code == 422 else 'created'}")

    if args.webhook:
        secret = (os.environ.get("ARCHIVIST_GH_WEBHOOK_SECRET") or "").strip()
        base = (os.environ.get("ARCHIVIST_GH_URL") or "").strip().rstrip("/")
        if not (secret and base):
            print("set ARCHIVIST_GH_WEBHOOK_SECRET and ARCHIVIST_GH_URL first")
            return 1
        url = f"{base}/github/webhook"
        _code, hooks = gh._request("GET", f"/repos/{slug}/hooks")
        match = [
            hook
            for hook in (hooks or [])
            if (hook.get("config") or {}).get("url") == url
        ]
        if match:
            print(f"webhook already exists: id={match[0]['id']} url={url}")
        else:
            _code, hook = gh._request(
                "POST",
                f"/repos/{slug}/hooks",
                body={
                    "name": "web",
                    "active": True,
                    "events": ["issues", "issue_comment"],
                    "config": {
                        "url": url,
                        "content_type": "json",
                        "secret": secret,
                        "insecure_ssl": "0",
                    },
                },
            )
            print(f"webhook created: id={(hook or {}).get('id')} url={url}")

    print()
    print("--- suggested .env lines ---")
    print(f"ARCHIVIST_GH_BOT={login}")
    print(f"ARCHIVIST_GH_REPOS={slug}=repo:falkordb-py")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GhError as exc:
        print(f"GitHub error: {exc}")
        raise SystemExit(1) from None
