"""GitHub webhook intake: verify the HMAC, dedupe, hand off to a worker.

The HTTP route must ack fast (GitHub times out at 10 s); the actual agent
run takes 30-90 s, so handlers execute on a small bounded thread pool and
report through the journal. Failures are logged, never raised into the
server thread.
"""

from __future__ import annotations

import collections
import hashlib
import hmac
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from . import handlers
from .config import GhConfig, load_config

_log = logging.getLogger("archivist.gh")

_seen: collections.OrderedDict[str, None] = collections.OrderedDict()
_seen_lock = threading.Lock()
_pool: ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()


def _remember(delivery: str) -> bool:
    """True when this delivery id is new (LRU of 512 ids)."""
    with _seen_lock:
        if delivery in _seen:
            return False
        _seen[delivery] = None
        while len(_seen) > 512:
            _seen.popitem(last=False)
        return True


def _executor(cfg: GhConfig) -> ThreadPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(
                max_workers=max(1, cfg.workers),
                thread_name_prefix="gh-worker",
            )
        return _pool


def verify_signature(secret: str, raw: bytes, header: str | None) -> bool:
    if not secret or not header or not header.startswith("sha256="):
        return False
    mac = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest("sha256=" + mac, header)


def _header(headers: dict, name: str) -> str | None:
    lowered = name.lower()
    for key, value in (headers or {}).items():
        if key.lower() == lowered:
            return value
    return None


def _run(cfg: GhConfig, event: str, payload: dict, graph: str) -> None:
    try:
        handlers.handle_event(cfg, event, payload, graph)
    except Exception:  # noqa: BLE001 - a worker must never kill the pool
        _log.exception("gh handler failed: %s graph=%s", event, graph)


def handle_webhook(
    headers: dict, raw: bytes, cfg: GhConfig | None = None
) -> tuple[int, dict]:
    """Verify + enqueue one delivery; returns (HTTP status, JSON payload)."""
    cfg = cfg or load_config()
    if not cfg.enabled:
        return 200, {"status": "disabled", "detail": "ARCHIVIST_GH_* not set"}
    if not verify_signature(cfg.secret, raw, _header(headers, "x-hub-signature-256")):
        _log.warning("webhook rejected: bad signature")
        return 401, {"status": "bad_signature"}
    event = _header(headers, "x-github-event") or ""
    delivery = _header(headers, "x-github-delivery") or ""
    if event == "ping":
        return 200, {"status": "pong"}
    if event not in ("issues", "issue_comment"):
        return 202, {"status": "ignored", "event": event}
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except ValueError:
        return 400, {"status": "bad_payload"}
    slug = ((payload.get("repository") or {}).get("full_name") or "").lower()
    graph = cfg.graph_for(slug) if slug else None
    if not graph:
        return 202, {"status": "ignored", "detail": "repo not mapped"}
    if delivery and not _remember(delivery):
        return 202, {"status": "duplicate"}
    action = payload.get("action")
    if (event == "issues" and action != "opened") or (
        event == "issue_comment" and action != "created"
    ):
        return 202, {"status": "ignored", "detail": f"action={action}"}
    _executor(cfg).submit(_run, cfg, event, payload, graph)
    _log.info("accepted %s/%s for %s -> %s", event, action, slug, graph)
    return 202, {"status": "accepted", "event": event, "repo": slug}
