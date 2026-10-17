#!/usr/bin/env python3
"""Stream a live ask against a running console — the M7b SSE smoke (M7).

Usage:
    python scripts/stream_smoke.py [--url http://127.0.0.1:8787] [--question ...]
    python scripts/stream_smoke.py --url https://<tunnel-host> --question "..."

Reads the SSE response frame by frame (unbuffered) and prints every frame
with a millisecond timestamp, so a saved transcript proves the stream is
incremental — step frames and answer deltas arrive as they happen, not as
one blob at the end. A quiet stream shows ``: ping`` heartbeats.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://127.0.0.1:8787")
    parser.add_argument(
        "--question",
        default="Who calls `app_context` in this repository, and which files "
        "should be reviewed if its behaviour changed?",
    )
    parser.add_argument("--timeout", type=float, default=240.0)
    args = parser.parse_args()

    payload = json.dumps({"question": args.question, "remember": False}).encode()
    request = urllib.request.Request(
        args.url.rstrip("/") + "/api/ask/stream",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.time()
    print(f"$ POST {args.url.rstrip('/')}/api/ask/stream")
    print(f"  question: {args.question!r}")
    frames = 0
    with urllib.request.urlopen(request, timeout=args.timeout) as response:
        stamp = f"[{(time.time() - started) * 1000:7.0f} ms]"
        print(
            f"{stamp} HTTP {response.status} " f"{response.headers.get('Content-Type')}"
        )
        event = None
        for raw in response:
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            stamp = f"[{(time.time() - started) * 1000:7.0f} ms]"
            if line.startswith(":"):
                print(f"{stamp} heartbeat {line}")
                continue
            if line.startswith("event:"):
                event = line[6:].strip()
                continue
            if not line.startswith("data:"):
                continue
            data = json.loads(line[5:].strip())
            frames += 1
            if event == "answer_delta":
                print(f"{stamp} answer_delta {data['text']!r}")
            elif event == "step_start":
                args_text = json.dumps(data.get("args") or {})
                print(f"{stamp} step_start  #{data['i']} {data['tool']} {args_text}")
            elif event == "step_result":
                print(
                    f"{stamp} step_result #{data['i']} {data['tool']} "
                    f"status={data['status']} count={data['count']} "
                    f"ms={data['ms']}"
                )
                for note in data.get("notes") or []:
                    print(f"{stamp}   note: {note}")
            elif event == "done":
                answer = data.get("answer") or {}
                print(f"{stamp} done  text={answer.get('text', '')!r}")
                print(
                    f"         steps={len(answer.get('steps') or [])} "
                    f"queries={len(answer.get('queries') or [])} "
                    f"partial={answer.get('partial')} "
                    f"error={answer.get('error')}"
                )
                for warning in data.get("warnings") or []:
                    print(f"         warning: {warning}")
            elif event == "error":
                print(f"{stamp} error {data.get('message')}")
            else:
                print(f"{stamp} {event} {json.dumps(data)[:200]}")
    print(f"-- {frames} frames in {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
