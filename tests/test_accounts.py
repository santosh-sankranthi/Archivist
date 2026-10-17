"""Tests for the M6 account store — hermetic, tmp dirs, injectable clock."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from archivist.accounts import (
    LOGIN_LIMIT,
    LOGIN_WINDOW,
    SESSION_TTL,
    SIGNUP_LIMIT,
    AccountError,
    AccountStore,
    clear_cookie,
    session_cookie,
)


class FakeClock:
    def __init__(self, now: int = 1_700_000_000):
        self.now = now

    def __call__(self) -> float:
        return float(self.now)

    def advance(self, seconds: int) -> None:
        self.now += seconds


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def store(tmp_path: Path, clock: FakeClock) -> AccountStore:
    return AccountStore(tmp_path / "data", clock=clock)


def _signup(
    store: AccountStore, name: str = "santosh", password: str = "correct horse"
):
    return store.signup(name, password, ip="203.0.113.9")


# -- signup ----------------------------------------------------------------


def test_signup_persists_hashed_user(store: AccountStore):
    user = _signup(store)
    assert user.username == "santosh"
    assert len(user.uid) == 8

    path = store.accounts_dir / "users.json"
    raw = path.read_text(encoding="utf-8")
    assert "correct horse" not in raw
    record = json.loads(raw)["users"][user.uid]
    assert record["salt"] and record["hash"]
    assert record["hash"] != "correct horse"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert store.user(user.uid) == user


def test_signup_normalizes_case_and_space(store: AccountStore):
    user = store.signup("  Santosh_1 ", "correct horse")
    assert user.username == "santosh_1"


@pytest.mark.parametrize(
    "name",
    ["ab", "a" * 25, "has space", "emoji⚡", "", "semi;colon"],
)
def test_signup_rejects_bad_usernames(store: AccountStore, name: str):
    with pytest.raises(AccountError) as exc:
        store.signup(name, "correct horse", ip=f"ip-{name}")
    assert exc.value.status == 400
    assert exc.value.code == "bad_username"


@pytest.mark.parametrize("password", ["short", "ninechars", "x" * 201])
def test_signup_rejects_bad_passwords(store: AccountStore, password: str):
    with pytest.raises(AccountError) as exc:
        store.signup("santosh", password, ip=f"ip-{len(password)}")
    assert exc.value.status == 400
    assert exc.value.code == "bad_password"


def test_signup_rejects_duplicate_case_insensitively(store: AccountStore):
    store.signup("Santosh", "correct horse", ip="a")
    with pytest.raises(AccountError) as exc:
        store.signup("santosh", "another pass", ip="b")
    assert exc.value.status == 409
    assert exc.value.code == "username_taken"


def test_signup_rate_limited_per_ip(store: AccountStore):
    for index in range(SIGNUP_LIMIT):
        store.signup(f"user{index}", "correct horse", ip="198.51.100.7")
    with pytest.raises(AccountError) as exc:
        store.signup("late", "correct horse", ip="198.51.100.7")
    assert exc.value.status == 429
    # A different ip is unaffected.
    assert store.signup("other", "correct horse", ip="198.51.100.8").username == "other"


# -- login -----------------------------------------------------------------


def test_login_verifies_and_is_generic(store: AccountStore):
    user = _signup(store)
    assert store.authenticate("SANTOSH", "correct horse", ip="a") == user

    with pytest.raises(AccountError) as wrong:
        store.authenticate("santosh", "wrong horse!", ip="b")
    with pytest.raises(AccountError) as missing:
        store.authenticate("nobody", "wrong horse!", ip="c")
    assert wrong.value.status == missing.value.status == 401
    assert (
        wrong.value.message == missing.value.message == "invalid username or password"
    )


def test_login_rate_limit_then_window_resets(store: AccountStore, clock: FakeClock):
    _signup(store)
    for _ in range(LOGIN_LIMIT):
        with pytest.raises(AccountError):
            store.authenticate("santosh", "wrong horse!", ip="9.9.9.9")
    with pytest.raises(AccountError) as exc:
        store.authenticate("santosh", "correct horse", ip="9.9.9.9")
    assert exc.value.status == 429

    clock.advance(LOGIN_WINDOW + 1)
    assert (
        store.authenticate("santosh", "correct horse", ip="9.9.9.9").username
        == "santosh"
    )


def test_login_limits_are_per_ip(store: AccountStore):
    _signup(store)
    for _ in range(LOGIN_LIMIT):
        with pytest.raises(AccountError):
            store.authenticate("santosh", "wrong", ip="one")
    with pytest.raises(AccountError) as exc:
        store.authenticate("santosh", "correct horse", ip="one")
    assert exc.value.status == 429
    assert (
        store.authenticate("santosh", "correct horse", ip="two").username == "santosh"
    )


# -- sessions --------------------------------------------------------------


def test_session_roundtrip_stores_only_hash(store: AccountStore):
    user = _signup(store)
    token, expires = store.open_session(user)
    assert expires > 0 and len(token) > 20

    raw = (store.accounts_dir / "sessions.json").read_text(encoding="utf-8")
    assert token not in raw  # only SHA-256(token) is persisted
    assert store.session_user(token) == user
    assert store.session_user(token + "x") is None
    assert store.session_user(None) is None


def test_session_expires_after_ttl(store: AccountStore, clock: FakeClock):
    user = _signup(store)
    token, _ = store.open_session(user)
    # No touch in between: the window is absolute until something resolves it.
    clock.advance(SESSION_TTL + 1)
    assert store.session_user(token) is None


def test_session_slides_on_touch(store: AccountStore, clock: FakeClock):
    user = _signup(store)
    token, _ = store.open_session(user)
    clock.advance(SESSION_TTL - 10)
    info = store.session_info(token)
    assert info is not None and info.refreshed is True
    assert info.expires_at == clock.now + SESSION_TTL
    # Within the touch interval no extra refresh is claimed.
    clock.advance(5)
    assert store.session_info(token).refreshed is False


def test_login_issues_fresh_token_and_logout_kills_one(store: AccountStore):
    user = _signup(store)
    first, _ = store.open_session(user)
    second, _ = store.open_session(user)
    assert first != second

    store.close_session(second)
    assert store.session_user(second) is None
    assert store.session_user(first) == user


def test_logout_missing_token_is_a_noop(store: AccountStore):
    store.close_session(None)
    store.close_session("never-existed")


def test_session_with_deleted_user_is_invalid(store: AccountStore):
    user = _signup(store)
    token, _ = store.open_session(user)
    data = store._users()
    data["users"].pop(user.uid)
    store._write(store.accounts_dir / "users.json", data)
    assert store.session_user(token) is None


# -- cookies ---------------------------------------------------------------


def test_cookie_flags_http_and_https():
    plain = session_cookie("tok", secure=False)
    assert "HttpOnly" in plain and "SameSite=Lax" in plain and "Secure" not in plain
    assert "Max-Age=0" not in plain

    secure = session_cookie("tok", secure=True)
    assert "Secure" in secure

    cleared = clear_cookie(secure=True)
    assert "Max-Age=0" in cleared and "Secure" in cleared
    assert "archivist_sid=" in cleared


# -- repo registry + resolution -------------------------------------------


def _repo(repo_id: str = "r1", status: str = "ready") -> dict:
    return {
        "id": repo_id,
        "name": "Flask",
        "slug": "flask",
        "graph": f"user:{repo_id}",
        "status": status,
        "source_url": "https://github.com/pallets/flask",
    }


def test_repo_registry_roundtrip(store: AccountStore):
    user = _signup(store)
    assert store.repos(user.uid) == []
    store.write_repos(user.uid, [_repo()])
    assert store.repos(user.uid) == [_repo()]


def test_repo_registry_refuses_bad_uid_and_overflow(store: AccountStore):
    user = _signup(store)
    with pytest.raises(ValueError):
        store.write_repos("../etc", [])
    with pytest.raises(AccountError):
        store.write_repos(user.uid, [_repo("a"), _repo("b"), _repo("c")])


def test_repo_registry_ignores_malformed_entries(store: AccountStore):
    user = _signup(store)
    store.write_repos(user.uid, [_repo()])
    path = store.users_dir / user.uid / "repos.json"
    data = json.loads(path.read_text())
    data["repos"].append({"id": 7, "graph": None})
    path.write_text(json.dumps(data))
    assert store.repos(user.uid) == [_repo()]


def test_resolve_graph_anonymous_always_demo(store: AccountStore):
    graph, repo = store.resolve_graph(None, "r1", demo_graph="repo:flask")
    assert (graph, repo) == ("repo:flask", None)


def test_resolve_graph_signed_in_defaults_and_selects(store: AccountStore):
    user = _signup(store)
    store.write_repos(user.uid, [_repo("r1", status="ingesting"), _repo("r2")])

    assert store.resolve_graph(user, None, demo_graph="repo:flask")[0] == "user:r2"
    assert store.resolve_graph(user, "r2", demo_graph="repo:flask")[0] == "user:r2"
    with pytest.raises(AccountError) as exc:
        store.resolve_graph(user, "repo:flask", demo_graph="repo:flask")
    assert exc.value.status == 404


def test_resolve_graph_user_without_repos_gets_demo(store: AccountStore):
    user = _signup(store)
    assert store.resolve_graph(user, None, demo_graph="repo:flask") == (
        "repo:flask",
        None,
    )


def test_resolve_graph_foreign_repo_id_is_404(store: AccountStore):
    alice = store.signup("alice", "correct horse", ip="a")
    bob = store.signup("bob_2", "correct horse", ip="b")
    store.write_repos(bob.uid, [_repo("bob-repo")])
    with pytest.raises(AccountError) as exc:
        store.resolve_graph(alice, "bob-repo", demo_graph="repo:flask")
    assert exc.value.status == 404
