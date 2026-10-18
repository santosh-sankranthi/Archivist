"""Minimal GitHub REST client (stdlib only — the box has no requests).

Only the calls the community agent needs; every mutating call is skipped
when ``dry_run`` is set (the handler logs what would have been posted).
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
UA = "archivist-community-agent"
_log = logging.getLogger("archivist.gh.client")


class GhError(RuntimeError):
    """A GitHub call failed in a way the caller must know about."""


class GitHub:
    def __init__(self, token: str, *, dry_run: bool = False, timeout: int = 25):
        self.token = token
        self.dry_run = dry_run
        self.timeout = timeout

    # -- plumbing ----------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        params: dict | None = None,
        expect: tuple[int, ...] = (200, 201, 202, 204),
    ):
        url = f"{API}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": UA,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                payload = json.loads(raw.decode("utf-8")) if raw else None
                return resp.status, payload
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else None
            except ValueError:
                payload = {"message": raw.decode("utf-8", "replace")[:300]}
            if exc.code in expect:
                return exc.code, payload
            message = (payload or {}).get("message", "")
            raise GhError(f"{method} {path} -> HTTP {exc.code}: {message}") from None
        except urllib.error.URLError as exc:
            raise GhError(f"{method} {path} -> {exc.reason}") from None

    # -- reads -------------------------------------------------------------

    def get_user(self) -> dict:
        _code, payload = self._request("GET", "/user")
        return payload or {}

    def get_repo(self, slug: str) -> dict | None:
        code, payload = self._request(
            "GET", f"/repos/{slug}", expect=(200, 404)
        )
        return payload if code == 200 else None

    def search_issues(self, slug: str, text: str, limit: int = 3) -> list[dict]:
        query = f"repo:{slug} is:issue {text}".strip()
        _code, payload = self._request(
            "GET", "/search/issues", params={"q": query, "per_page": limit}
        )
        return list((payload or {}).get("items") or [])

    # -- writes (dry-run aware) -------------------------------------------

    def post_comment(self, slug: str, number: int, body: str) -> dict:
        if self.dry_run:
            _log.warning(
                "DRY post_comment %s#%s:\n%s", slug, number, body[:800]
            )
            return {"html_url": "(dry-run)"}
        _code, payload = self._request(
            "POST", f"/repos/{slug}/issues/{number}/comments", body={"body": body}
        )
        return payload or {}

    def add_labels(self, slug: str, number: int, labels: list[str]) -> None:
        if not labels:
            return
        if self.dry_run:
            _log.warning("DRY add_labels %s#%s: %s", slug, number, labels)
            return
        try:
            self._request(
                "POST",
                f"/repos/{slug}/issues/{number}/labels",
                body={"labels": labels},
            )
        except GhError as exc:  # a missing label must not kill the reply
            _log.warning("add_labels failed: %s", exc)

    def create_issue(
        self, slug: str, title: str, body: str, labels: list[str] | None = None
    ) -> dict:
        payload: dict = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        if self.dry_run:
            _log.warning("DRY create_issue %s: %s", slug, title)
            return {"html_url": "(dry-run)", "number": 0}
        _code, data = self._request("POST", f"/repos/{slug}/issues", body=payload)
        return data or {}
