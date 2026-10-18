#!/usr/bin/env python3
"""Sign and POST a Slack Events API request (route testing).

Usage:
  .venv/bin/python scripts/devtools/fake_slack.py --verify
  .venv/bin/python scripts/devtools/fake_slack.py --mention \
      --text "How do I create a vector index?" --channel C0123ABCD
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
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


def _post(url: str, secret: str, payload: dict) -> int:
    ts = str(int(time.time()))
    raw = json.dumps(payload).encode("utf-8")
    base = f"v0:{ts}:".encode() + raw
    sig = "v0=" + hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        f"{url}/slack/events",
        data=raw,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": sig,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"HTTP {resp.status}: {resp.read().decode('utf-8')}")
            return 0
    except Exception as exc:  # noqa: BLE001
        print(f"request failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true", help="url_verification")
    parser.add_argument("--mention", action="store_true", help="app_mention")
    parser.add_argument("--text", default="How do I create a vector index?")
    parser.add_argument("--channel", default="C-DEMO")
    parser.add_argument("--url", default="http://127.0.0.1:8787")
    args = parser.parse_args()
    secret = (os.environ.get("ARCHIVIST_SLACK_SIGNING_SECRET") or "").strip()
    if not secret:
        secret = "probe-secret"
        print("(signing secret not set yet — using probe value; route is in bootstrap mode)")
    if args.verify or not args.mention:
        payload = {"type": "url_verification", "challenge": "archivist-probe"}
        return _post(args.url, secret, payload)
    payload = {
        "type": "event_callback",
        "event": {
            "type": "app_mention",
            "user": "U-DEMO",
            "text": f"<@U-BOT> {args.text}",
            "channel": args.channel,
            "ts": f"{time.time():.6f}",
        },
    }
    return _post(args.url, secret, payload)


if __name__ == "__main__":
    raise SystemExit(main())
