"""Slack Events API intake: verify, answer the challenge, enqueue workers.

Slack expects an HTTP 200 within ~3 s and retries otherwise; the agent run
takes 30-90 s, so event callbacks go to a small worker pool. Signature is
``v0`` HMAC over ``v0:{ts}:{raw_body}`` with the signing secret, timestamp
window ±5 min; retry deliveries (X-Slack-Retry-Num) are ignored to avoid
duplicate replies. Before the signing secret exists (app setup), only the
``url_verification`` challenge is answered — everything else stays closed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import handlers
from .config import SlackConfig, load_config

_log = logging.getLogger("archivist.slack")

_MAX_SKEW_S = 300
_pool: ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()


def _executor() -> ThreadPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(
                max_workers=2, thread_name_prefix="slack-worker"
            )
        return _pool


def verify_signature(
    secret: str, raw: bytes, timestamp: str, signature: str | None
) -> bool:
    if not (secret and timestamp and signature):
        return False
    try:
        age = abs(time.time() - int(timestamp))
    except ValueError:
        return False
    if age > _MAX_SKEW_S:
        return False
    base = f"v0:{timestamp}:".encode() + raw
    mac = hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest("v0=" + mac, signature)


def _header(headers: dict, name: str) -> str | None:
    lowered = name.lower()
    for key, value in (headers or {}).items():
        if key.lower() == lowered:
            return value
    return None


def _run(cfg: SlackConfig, event: dict) -> None:
    try:
        handlers.handle_event(cfg, event)
    except Exception:  # noqa: BLE001 - a worker must never kill the pool
        _log.exception("slack handler failed: %s", event.get("type"))


def handle_request(
    headers: dict, raw: bytes, cfg: SlackConfig | None = None
) -> tuple[int, dict]:
    """Verify + handle one Events API request; returns (status, payload)."""
    cfg = cfg or load_config()
    if cfg.signing_secret:
        timestamp = _header(headers, "x-slack-request-timestamp") or ""
        signature = _header(headers, "x-slack-signature")
        if not verify_signature(cfg.signing_secret, raw, timestamp, signature):
            _log.warning("slack event rejected: bad signature")
            return 401, {"status": "bad_signature"}
    else:
        _log.warning("slack signing secret not set — bootstrap mode (challenge only)")
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except ValueError:
        return 400, {"status": "bad_payload"}
    if payload.get("type") == "url_verification":
        return 200, {"challenge": payload.get("challenge")}
    if _header(headers, "x-slack-retry-num"):
        return 200, {"status": "retry_ignored"}
    if not cfg.enabled:
        return 200, {"status": "disabled", "detail": "ARCHIVIST_SLACK_* not set"}
    if payload.get("type") != "event_callback":
        return 200, {"status": "ignored", "type": payload.get("type")}
    event = payload.get("event") or {}
    etype = event.get("type") or ""
    if event.get("bot_id") or event.get("subtype"):
        return 200, {"status": "ignored", "detail": "bot/subtype"}
    if etype == "app_mention" or etype == "message" and event.get("channel_type") == "im":
        pass
    else:
        return 200, {"status": "ignored", "type": etype}
    channel = event.get("channel") or ""
    if not cfg.channel_ok(channel):
        return 200, {"status": "ignored", "detail": "channel not allowed"}
    _executor().submit(_run, cfg, event)
    _log.info("slack accepted %s in %s", etype, channel)
    return 200, {"status": "accepted"}
