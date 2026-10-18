#!/usr/bin/env python3
"""Slack bootstrap probe: token validity, bot identity, visible channels.

Usage:
  .venv/bin/python scripts/devtools/slack_probe.py
  .venv/bin/python scripts/devtools/slack_probe.py --post C0123 hello there
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
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

API = "https://slack.com/api"


def call(method: str, token: str, **params) -> dict:
    body = json.dumps(params).encode("utf-8") if params else b""
    req = urllib.request.Request(
        f"{API}/{method}",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        return {"ok": False, "http": exc.code, "body": raw[:300]}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--post", nargs="*", default=None)
    args = parser.parse_args()
    token = os.environ.get("ARCHIVIST_SLACK_BOT_TOKEN", "").strip()
    if not token:
        print("ARCHIVIST_SLACK_BOT_TOKEN not set")
        return 1
    auth = call("auth.test", token)
    keys = ("ok", "error", "team", "user", "user_id", "bot_id", "team_id")
    print("auth.test:", json.dumps({k: auth.get(k) for k in keys if k in auth}))
    if not auth.get("ok"):
        return 1
    conv = call(
        "conversations.list",
        token,
        types="public_channel,private_channel",
        limit=50,
        exclude_archived=True,
    )
    if conv.get("ok"):
        channels = conv.get("channels") or []
        names = [f"#{c.get('name')} ({c.get('id')})" for c in channels]
        print("bot channels:", names or "none - invite it with /invite @archivist")
    else:
        print("conversations.list:", conv.get("error"))
    if args.post:
        channel = args.post[0]
        text = " ".join(args.post[1:]) or "probe hello from archivist"
        sent = call("chat.postMessage", token, channel=channel, text=text)
        keys2 = ("ok", "error", "channel", "ts")
        print("chat.postMessage:", json.dumps({k: sent.get(k) for k in keys2 if k in sent}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
