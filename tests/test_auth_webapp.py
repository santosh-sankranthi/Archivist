"""HTTP-level auth tests (M6): cookies, CSRF, isolation, honest refusals."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from archivist.accounts import AccountStore, User
from archivist.webapp import ApiError, RequestContext, WebApp, make_server

PASSWORD = "correct horse battery"


def _call(
    url: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    headers: dict | None = None,
) -> tuple[int, dict, dict]:
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


def _token(headers: dict) -> str | None:
    raw = headers.get("Set-Cookie")
    if not raw:
        return None
    first = raw.split(";")[0]
    name, _, value = first.partition("=")
    return value if name == "archivist_sid" and value else None


@pytest.fixture()
def auth_server(tmp_path: Path):
    store = AccountStore(tmp_path / "data")
    app = WebApp(accounts=store, graph="repo:flask")
    server = make_server(app, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_anonymous_me(auth_server: str):
    status, _headers, body = _call(auth_server + "/api/me")
    assert status == 200
    assert body["anonymous"] is True
    assert body["user"] is None
    assert body["repos"] == []
    assert body["graph"] == "repo:flask"


def test_signup_sets_cookie_and_me(auth_server: str):
    status, headers, body = _call(
        auth_server + "/api/auth/signup",
        method="POST",
        payload={"username": "santosh", "password": PASSWORD},
    )
    assert status == 200, body
    assert body["user"]["username"] == "santosh"
    token = _token(headers)
    assert token
    cookie = headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert "Secure" not in cookie  # plain http in tests

    status, _headers, me = _call(
        auth_server + "/api/me", headers={"Cookie": f"archivist_sid={token}"}
    )
    assert status == 200
    assert me["user"]["username"] == "santosh"
    assert me["anonymous"] is False


def test_login_and_logout_lifecycle(auth_server: str):
    _call(
        auth_server + "/api/auth/signup",
        method="POST",
        payload={"username": "santosh", "password": PASSWORD},
    )
    status, headers, body = _call(
        auth_server + "/api/auth/login",
        method="POST",
        payload={"username": "santosh", "password": PASSWORD},
    )
    assert status == 200, body
    token = _token(headers)
    assert token

    status, headers, body = _call(
        auth_server + "/api/auth/logout",
        method="POST",
        payload={},
        headers={"Cookie": f"archivist_sid={token}"},
    )
    assert status == 200 and body["ok"] is True
    assert _token(headers) is None
    assert "Max-Age=0" in headers["Set-Cookie"]

    status, _headers, me = _call(
        auth_server + "/api/me", headers={"Cookie": f"archivist_sid={token}"}
    )
    assert me["anonymous"] is True


def test_login_is_generic_and_rate_limited(auth_server: str):
    _call(
        auth_server + "/api/auth/signup",
        method="POST",
        payload={"username": "santosh", "password": PASSWORD},
    )
    for _ in range(5):
        status, _headers, body = _call(
            auth_server + "/api/auth/login",
            method="POST",
            payload={"username": "santosh", "password": "not the password"},
        )
        assert status == 401
        assert body["error"] == "bad_credentials"

    status, _headers, body = _call(
        auth_server + "/api/auth/login",
        method="POST",
        payload={"username": "santosh", "password": PASSWORD},
    )
    assert status == 429
    assert body["error"] == "rate_limited"


def test_signup_duplicate_and_validation(auth_server: str):
    first = {"username": "santosh", "password": PASSWORD}
    assert (
        _call(auth_server + "/api/auth/signup", method="POST", payload=first)[0] == 200
    )
    status, _headers, body = _call(
        auth_server + "/api/auth/signup", method="POST", payload=first
    )
    assert status == 409 and body["error"] == "username_taken"

    status, _headers, body = _call(
        auth_server + "/api/auth/signup",
        method="POST",
        payload={"username": "ab", "password": PASSWORD},
    )
    assert status == 400 and body["error"] == "bad_username"


def test_secure_cookie_only_behind_https_proxy(auth_server: str):
    status, headers, _body = _call(
        auth_server + "/api/auth/signup",
        method="POST",
        payload={"username": "santosh", "password": PASSWORD},
        headers={"X-Forwarded-Proto": "https"},
    )
    assert status == 200
    assert "Secure" in headers["Set-Cookie"]


def test_auth_rejects_cross_origin(auth_server: str):
    status, _headers, body = _call(
        auth_server + "/api/auth/signup",
        method="POST",
        payload={"username": "santosh", "password": PASSWORD},
        headers={"Origin": "https://evil.example"},
    )
    assert status == 403
    assert body["error"] == "bad_origin"


def test_auth_requires_json_content_type(auth_server: str):
    request = urllib.request.Request(
        auth_server + "/api/auth/signup",
        data=json.dumps({"username": "santosh", "password": PASSWORD}).encode(),
        headers={"Content-Type": "text/plain"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request, timeout=15)
    assert exc.value.code == 415
    assert json.loads(exc.value.read())["error"] == "json_required"


def test_signup_can_be_closed(tmp_path: Path):
    store = AccountStore(tmp_path / "data")
    app = WebApp(accounts=store, graph="repo:flask", signup="closed")
    server = make_server(app, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _headers, body = _call(
            f"http://127.0.0.1:{server.server_port}/api/auth/signup",
            method="POST",
            payload={"username": "santosh", "password": PASSWORD},
        )
        assert status == 403
        assert body["error"] == "signup_closed"
    finally:
        server.shutdown()
        server.server_close()


def test_auth_endpoints_disabled_without_a_store():
    app = WebApp(graph="repo:flask")
    status, _content_type, payload = app.dispatch(
        "POST",
        "/api/auth/signup",
        {},
        {"username": "santosh", "password": PASSWORD},
        ctx=RequestContext(headers={"Content-Type": "application/json"}),
    )
    assert status == 503
    assert json.loads(payload)["error"] == "accounts_disabled"


# -- graph isolation (the security boundary, without a database) -----------


def _user(store: AccountStore, name: str) -> User:
    return store.signup(name, PASSWORD, ip=f"ip-{name}")


def test_graph_resolution_is_per_session(tmp_path: Path):
    store = AccountStore(tmp_path / "data")
    alice = _user(store, "alice")
    bob = _user(store, "bob_2")
    store.write_repos(
        alice.uid,
        [{"id": "a1", "graph": "user:alice", "status": "ready"}],
    )
    store.write_repos(
        bob.uid,
        [{"id": "b1", "graph": "user:bob", "status": "ready"}],
    )
    app = WebApp(graph="repo:flask", accounts=store, demo_graph="repo:flask")

    try:
        app._enter(RequestContext(user=alice), "/api/graph/overview", {"repo": ["a1"]})
        assert app.graph == "user:alice"
        with pytest.raises(ApiError) as exc:
            app._enter(
                RequestContext(user=bob), "/api/graph/overview", {"repo": ["a1"]}
            )
        assert exc.value.status == 404
    finally:
        app._leave()

    app._enter(RequestContext(user=None), "/api/graph/overview", {"repo": ["a1"]})
    try:
        assert app.graph == "repo:flask"  # anonymous never leaves the demo
    finally:
        app._leave()


def test_raw_graph_names_are_never_accepted(tmp_path: Path):
    store = AccountStore(tmp_path / "data")
    alice = _user(store, "alice")
    app = WebApp(graph="repo:flask", accounts=store, demo_graph="repo:flask")
    try:
        with pytest.raises(ApiError):
            app._enter(
                RequestContext(user=alice),
                "/api/graph/overview",
                {"repo": ["repo:flask"]},
            )
    finally:
        app._leave()
