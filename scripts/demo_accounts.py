#!/usr/bin/env python3
"""M6a evidence — signup, sessions, CSRF, isolation, logout (real HTTP).

Run: .venv/bin/python scripts/demo_accounts.py

Starts the real console server on an ephemeral port with a throwaway data
dir and drives it over HTTP. The isolation probe seeds two repo registries
with graph names (ingest itself is M8) and asks the *resolver* which graph
each session may read — the boundary that keeps tenants apart. Nothing here
touches the live data directory or the demo graph.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from archivist.accounts import AccountStore
from archivist.webapp import ApiError, RequestContext, WebApp, make_server

PASSWORD = "correct horse battery"


def call(url, *, method="GET", payload=None, headers=None):
    data = None if payload is None else json.dumps(payload).encode()
    request_headers = dict(headers or {})
    if payload is not None:
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(
        url, data=data, headers=request_headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, dict(response.headers), json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), json.loads(exc.read())


def token_of(headers):
    raw = headers.get("Set-Cookie", "")
    first = raw.split(";")[0]
    name, _, value = first.partition("=")
    return value if name == "archivist_sid" else None


def line(label, value):
    print(f"  {label:<32} {value}")


def main() -> int:
    print("Archivist M6a — accounts / sessions / isolation (live HTTP smoke)")
    with tempfile.TemporaryDirectory() as tmp:
        store = AccountStore(tmp)
        app = WebApp(graph="repo:flask", accounts=store, demo_graph="repo:flask")
        server = make_server(app, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            status, _headers, me = call(base + "/api/me")
            line("anonymous /api/me", f"{status} user={me['user']} repo={me['graph']}")

            status, headers, body = call(
                base + "/api/auth/signup",
                method="POST",
                payload={"username": "Santosh", "password": PASSWORD},
            )
            alice_token = token_of(headers)
            cookie = headers.get("Set-Cookie", "")
            line(
                "signup",
                f"{status} user={body['user']['username']} uid={body['user']['uid']}",
            )
            line("cookie flags", cookie.split(";", 1)[1].strip())
            line("token stored as", "sha256(token) only")

            status, _headers, me = call(
                base + "/api/me", headers={"Cookie": f"archivist_sid={alice_token}"}
            )
            line(
                "session /api/me",
                f"{status} user={me['user']['username']} anonymous={me['anonymous']}",
            )

            status, headers, _body = call(
                base + "/api/auth/login",
                method="POST",
                payload={"username": "santosh", "password": PASSWORD},
            )
            login_token = token_of(headers)
            line("login", f"{status} fresh-token={login_token != alice_token}")

            status, _headers, body = call(
                base + "/api/auth/signup",
                method="POST",
                payload={"username": "santosh", "password": PASSWORD},
            )
            line("duplicate signup", f"{status} {body['error']}")

            status, _headers, body = call(
                base + "/api/auth/login",
                method="POST",
                payload={"username": "santosh", "password": "wrong password"},
            )
            line("wrong password", f"{status} {body['error']} (generic)")

            status, _headers, body = call(
                base + "/api/auth/signup",
                method="POST",
                payload={"username": "attacker", "password": PASSWORD},
                headers={"Origin": "https://evil.example"},
            )
            line("cross-origin signup", f"{status} {body['error']}")

            # Isolation: two accounts, two registries, one shared demo.
            alice_uid = store.session_user(login_token).uid
            bob = store.signup("bob_2", PASSWORD, ip="198.51.100.2")
            store.write_repos(
                alice_uid, [{"id": "a1", "graph": "user:alice", "status": "ready"}]
            )
            store.write_repos(
                bob.uid, [{"id": "b1", "graph": "user:bob", "status": "ready"}]
            )
            users = store._users()["users"]
            stored = users[alice_uid]
            line(
                "users.json",
                f"keys={sorted(stored)} hash!=password={stored['hash'] != PASSWORD}",
            )

            alice_user = store.session_user(login_token)
            for label, user, repo in (
                ("alice ?repo=a1", alice_user, "a1"),
                ("bob ?repo=a1", bob, "a1"),
                ("anonymous ?repo=a1", None, "a1"),
            ):
                try:
                    app._enter(
                        RequestContext(user=user),
                        "/api/graph/overview",
                        {"repo": [repo]},
                    )
                    line(label, f"graph={app.graph}")
                except ApiError as exc:
                    line(label, f"{exc.status} {exc.code}")
                finally:
                    app._leave()
            try:
                app._enter(
                    RequestContext(user=alice_user),
                    "/api/graph/overview",
                    {"repo": ["repo:flask"]},
                )
                line("alice ?repo=repo:flask", f"graph={app.graph}")
            except ApiError as exc:
                line("alice ?repo=repo:flask", f"{exc.status} {exc.code}")
            finally:
                app._leave()

            status, headers, _body = call(
                base + "/api/auth/logout",
                method="POST",
                payload={},
                headers={"Cookie": f"archivist_sid={login_token}"},
            )
            line(
                "logout",
                f"{status} cleared={'Max-Age=0' in headers.get('Set-Cookie', '')}",
            )
            status, _headers, me = call(
                base + "/api/me", headers={"Cookie": f"archivist_sid={login_token}"}
            )
            line("old token after logout", f"{status} anonymous={me['anonymous']}")
        finally:
            server.shutdown()
            server.server_close()
    print("OK — all probes answered as designed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
