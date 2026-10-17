"""Accounts, sessions and per-user repo registries (M6).

Why a JSON file store and not a database: identity must survive a graph wipe
(the demo graph is disposable; who owns what is not) and stay auditable by
the operator with ``cat``. The box is a single small VM, signups are rare,
and a signup/login costs one scrypt run — a file plus a lock is honest at
this scale. When it stops being honest, the interface here is small enough
to move behind a real store.

Security posture (see PRODUCT-PLAN §M6):

* Passwords: scrypt (N=2^15, r=8, p=1, 16-byte salt, 32-byte hash). Never
  stored or logged in clear. Login failures are generic and cost one scrypt
  run even for unknown users, so response time does not enumerate accounts.
* Sessions: 32-byte urlsafe tokens; only their SHA-256 hash is stored. 7-day
  sliding TTL, rotated on every login, deleted on logout.
* Rate limits: in-memory, per trust key. They reset on restart and are not
  shared between processes — an accepted limitation of a single-box demo,
  documented in ``docs/DEPLOY.md``. The key may be ``CF-Connecting-IP`` when
  the server is only reachable through the Cloudflare tunnel; a directly
  exposed port makes that header spoofable.
* Writes: atomic ``tmp + os.replace`` under one process-wide lock; files are
  chmod 0600 and directories 0700.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

USERNAME_RE = re.compile(r"^[a-z0-9_-]{3,24}$")
MIN_PASSWORD = 10
MAX_PASSWORD = 200

SESSION_COOKIE = "archivist_sid"
SESSION_TTL = 7 * 24 * 3600  # seconds
TOKEN_BYTES = 32
UID_BYTES = 4  # 8 hex chars

SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_SALT = 16
SCRYPT_DKLEN = 32
SCRYPT_MAXMEM = 64 * 1024 * 1024  # OpenSSL needs headroom above 128*N*r

LOGIN_LIMIT = 5
LOGIN_WINDOW = 15 * 60
#: Per-username cap across all client IPs: a distributed spray should not get
#: unlimited guesses just because it rotates addresses. Deliberately looser
#: than the per-IP cap so a shared NAT is not locked out by one bad actor.
LOGIN_USER_LIMIT = 20
SIGNUP_LIMIT = 3
SIGNUP_WINDOW = 60 * 60
#: Persist a session's ``last_seen`` at most this often (seconds). Sliding
#: TTL does not need per-request disk writes to be true.
TOUCH_INTERVAL = 60

_UID_RE = re.compile(r"^[0-9a-f]{8}$")


class AccountError(Exception):
    """Client-class account failure: HTTP status, stable code, message."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Session:
    """A resolved session: the user, its expiry, and whether it was touched."""

    user: User
    expires_at: int
    refreshed: bool


@dataclass(frozen=True)
class User:
    """A public view of one account (never carries the hash)."""

    uid: str
    username: str
    created_at: int

    def public(self) -> dict:
        return {
            "uid": self.uid,
            "username": self.username,
            "created_at": self.created_at,
        }


class RateLimiter:
    """Fixed-window-in-a-list limiter: at most *limit* hits per *window*.

    Keys are caller-provided (``login:<ip>``, ``signup:<ip>``). State is
    per-process and deliberately not persisted: a restart clearing counters
    is preferable to losing the box's write budget to a limits file.
    """

    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window: float) -> bool:
        """Record one attempt; return False when the limit is exhausted."""
        now = self._clock()
        with self._lock:
            hits = [t for t in self._hits.get(key, ()) if now - t < window]
            if len(hits) >= limit:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True

    def clear(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


def _hash_password(password: str, salt: bytes, *, n: int = SCRYPT_N) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
        maxmem=SCRYPT_MAXMEM,
    )


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_cookie(token: str, *, secure: bool, max_age: int = SESSION_TTL) -> str:
    """``Set-Cookie`` value for a live session."""
    parts = [
        f"{SESSION_COOKIE}={token}",
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
        f"Max-Age={max_age}",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def clear_cookie(*, secure: bool) -> str:
    """``Set-Cookie`` value that expires the session cookie client-side."""
    parts = [
        f"{SESSION_COOKIE}=",
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
        "Max-Age=0",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


class AccountStore:
    """File-backed accounts, sessions and per-user repo registries."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        clock: Callable[[], float] = time.time,
        scrypt_n: int = SCRYPT_N,
        max_repos: int = 2,
    ):
        self.root = Path(data_dir)
        self.accounts_dir = self.root / "accounts"
        self.users_dir = self.root / "users"
        self._clock = clock
        self._scrypt_n = scrypt_n
        self._max_repos = max(1, int(max_repos))
        self._lock = threading.RLock()
        self.limiter = RateLimiter(clock)

    # -- files -------------------------------------------------------------

    def now(self) -> int:
        """Current time as the store's clock sees it (injectable in tests)."""
        return int(self._clock())

    def _ensure(self, path: Path, mode: int = 0o700) -> None:
        path.mkdir(parents=True, exist_ok=True)
        try:
            path.chmod(mode)
        except OSError:  # pragma: no cover - exotic filesystems
            pass

    def _read(self, path: Path, empty: dict) -> dict:
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return empty
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{path} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise TypeError(f"{path} must hold a JSON object")
        return data

    def _write(self, path: Path, payload: dict) -> None:
        self._ensure(path.parent)
        # Unique temp names: two threads in one process can write the same
        # file under the store lock's protection, but a caller that forgets
        # the lock must still not collide with a sibling (Gate B W1).
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
        data = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
        try:
            path.chmod(0o600)
        except OSError:  # pragma: no cover
            pass

    def _users(self) -> dict:
        return self._read(self.accounts_dir / "users.json", {"version": 1, "users": {}})

    def _sessions(self) -> dict:
        return self._read(
            self.accounts_dir / "sessions.json", {"version": 1, "sessions": {}}
        )

    # -- users -------------------------------------------------------------

    @staticmethod
    def _check_username(username: object) -> str:
        if not isinstance(username, str):
            raise AccountError(400, "bad_username", "username must be a string")
        name = username.strip().lower()
        if not USERNAME_RE.fullmatch(name):
            raise AccountError(
                400,
                "bad_username",
                "username must be 3-24 chars of a-z, 0-9, _ or -",
            )
        return name

    @staticmethod
    def _check_password(password: object) -> str:
        if not isinstance(password, str):
            raise AccountError(400, "bad_password", "password must be a string")
        if len(password) < MIN_PASSWORD:
            raise AccountError(
                400, "bad_password", f"password must be at least {MIN_PASSWORD} chars"
            )
        if len(password) > MAX_PASSWORD:
            raise AccountError(
                400, "bad_password", f"password must be at most {MAX_PASSWORD} chars"
            )
        return password

    def signup(self, username: object, password: object, *, ip: str = "") -> User:
        """Create an account; raises AccountError on invalid or duplicate input."""
        name = self._check_username(username)
        secret = self._check_password(password)
        if not self.limiter.hit(f"signup:{ip}", SIGNUP_LIMIT, SIGNUP_WINDOW):
            raise AccountError(429, "rate_limited", "too many signups; try later")
        salt = secrets.token_bytes(SCRYPT_SALT)
        digest = _hash_password(secret, salt, n=self._scrypt_n)
        now = int(self._clock())
        with self._lock:
            data = self._users()
            users = data.setdefault("users", {})
            if any(u.get("username") == name for u in users.values()):
                raise AccountError(409, "username_taken", "that username is taken")
            uid = self._new_uid(users)
            users[uid] = {
                "uid": uid,
                "username": name,
                "salt": salt.hex(),
                "hash": digest.hex(),
                "created_at": now,
            }
            self._write(self.accounts_dir / "users.json", data)
            self._ensure(self.users_dir / uid)
        return User(uid=uid, username=name, created_at=now)

    def _new_uid(self, users: dict) -> str:
        while True:
            uid = secrets.token_hex(UID_BYTES)
            if uid not in users:
                return uid

    def _find(self, username: str) -> dict | None:
        for record in self._users().get("users", {}).values():
            if record.get("username") == username:
                return record
        return None

    def _verify(self, password: str, record: dict | None) -> bool:
        """Constant-work verification: unknown users still pay one scrypt."""
        if record is None:
            _hash_password(password, b"\x00" * SCRYPT_SALT, n=self._scrypt_n)
            return False
        try:
            salt = bytes.fromhex(record["salt"])
            expected = bytes.fromhex(record["hash"])
        except (KeyError, ValueError):
            return False
        return hmac.compare_digest(
            _hash_password(password, salt, n=self._scrypt_n), expected
        )

    def authenticate(self, username: object, password: object, *, ip: str = "") -> User:
        """Verify credentials; generic failure, rate-limited by client ip.

        Two counters: the per-IP budget is **never** cleared by a success — a
        successful login must not buy an attacker five more guesses for the
        next victim (Gate B C1). The per-username counter is cleared only when
        that same username authenticates, so honest retries recover while a
        spray against one name stays capped.
        """
        if not self.limiter.hit(f"login_ip:{ip}", LOGIN_LIMIT, LOGIN_WINDOW):
            raise AccountError(429, "rate_limited", "too many attempts; try later")
        name = username.strip().lower() if isinstance(username, str) else ""
        if name and not self.limiter.hit(
            f"login_user:{name[:64]}", LOGIN_USER_LIMIT, LOGIN_WINDOW
        ):
            raise AccountError(429, "rate_limited", "too many attempts; try later")
        if not isinstance(username, str) or not isinstance(password, str):
            raise AccountError(401, "bad_credentials", "invalid username or password")
        record = self._find(name) if USERNAME_RE.fullmatch(name) else None
        if not self._verify(password, record) or record is None:
            raise AccountError(401, "bad_credentials", "invalid username or password")
        if name:
            self.limiter.clear(f"login_user:{name[:64]}")
        return User(
            uid=record["uid"],
            username=record["username"],
            created_at=int(record["created_at"]),
        )

    def user(self, uid: str) -> User | None:
        record = self._users().get("users", {}).get(uid)
        if record is None:
            return None
        return User(
            uid=record["uid"],
            username=record["username"],
            created_at=int(record["created_at"]),
        )

    # -- sessions ----------------------------------------------------------

    def open_session(self, user: User) -> tuple[str, int]:
        """Create a fresh token for *user*; returns (token, expires_at)."""
        token = secrets.token_urlsafe(TOKEN_BYTES)
        now = int(self._clock())
        expires = now + SESSION_TTL
        with self._lock:
            data = self._sessions()
            sessions = data.setdefault("sessions", {})
            # Lazy hygiene: drop everything already expired on each login.
            for key in [
                k for k, v in sessions.items() if int(v.get("expires_at", 0)) <= now
            ]:
                sessions.pop(key, None)
            sessions[_token_hash(token)] = {
                "uid": user.uid,
                "created_at": now,
                "last_seen": now,
                "expires_at": expires,
            }
            self._write(self.accounts_dir / "sessions.json", data)
        return token, expires

    def session_info(self, token: str | None) -> Session | None:
        """Resolve a cookie token; slide and (throttled) persist its TTL."""
        if not token or len(token) > 200:
            return None
        key = _token_hash(token)
        now = int(self._clock())
        with self._lock:
            data = self._sessions()
            sessions = data.setdefault("sessions", {})
            record = sessions.get(key)
            if record is None:
                return None
            expiry = int(record.get("expires_at", 0))
            if expiry <= now:
                sessions.pop(key, None)
                self._write(self.accounts_dir / "sessions.json", data)
                return None
            user = self.user(str(record.get("uid", "")))
            if user is None:  # account removed out from under a token
                sessions.pop(key, None)
                self._write(self.accounts_dir / "sessions.json", data)
                return None
            refreshed = now - int(record.get("last_seen", 0)) >= TOUCH_INTERVAL
            if refreshed:
                record["last_seen"] = now
                record["expires_at"] = now + SESSION_TTL
                expiry = now + SESSION_TTL
                self._write(self.accounts_dir / "sessions.json", data)
            return Session(user=user, expires_at=expiry, refreshed=refreshed)

    def session_user(self, token: str | None) -> User | None:
        """Convenience wrapper: just the user for a live session."""
        info = self.session_info(token)
        return info.user if info else None

    def close_session(self, token: str | None) -> None:
        if not token:
            return
        key = _token_hash(token)
        with self._lock:
            data = self._sessions()
            sessions = data.setdefault("sessions", {})
            if key in sessions:
                sessions.pop(key, None)
                self._write(self.accounts_dir / "sessions.json", data)

    # -- per-user repos ----------------------------------------------------

    def repos(self, uid: str) -> list[dict]:
        """Registry for one user; malformed entries are ignored, not fatal."""
        if not _UID_RE.fullmatch(uid or ""):
            return []
        data = self._read(
            self.users_dir / uid / "repos.json", {"version": 1, "repos": []}
        )
        repos = data.get("repos")
        if not isinstance(repos, list):
            return []
        clean = []
        for item in repos:
            if (
                isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and isinstance(item.get("graph"), str)
                and _UID_RE.fullmatch(uid)
            ):
                clean.append(item)
        return clean[: self._max_repos]

    def write_repos(self, uid: str, repos: list[dict]) -> None:
        if not _UID_RE.fullmatch(uid or ""):
            raise ValueError(f"invalid uid {uid!r}")
        if len(repos) > self._max_repos:
            raise AccountError(
                409,
                "too_many_repos",
                f"at most {self._max_repos} repos per account",
            )
        # Same process-wide lock as every other store write: the registry is
        # live state, not an append-only log (Gate B W1).
        with self._lock:
            self._ensure(self.users_dir / uid)
            self._write(
                self.users_dir / uid / "repos.json",
                {"version": 1, "repos": repos},
            )

    def resolve_graph(
        self, user: User | None, repo_id: str | None, *, demo_graph: str
    ) -> tuple[str, dict | None]:
        """Pick the graph a request may read.

        Anonymous callers only ever see the demo graph. Signed-in callers may
        name *their own* repo id; an unknown or foreign id is a 404 — raw
        graph names from the client are never accepted. With no repo id the
        first ready repo wins, else the demo graph.
        """
        if user is None:
            return demo_graph, None
        repos = self.repos(user.uid)
        if repo_id:
            for repo in repos:
                if repo["id"] == repo_id:
                    return repo["graph"], repo
            raise AccountError(
                404, "no_such_repo", "no such repository in your account"
            )
        for repo in repos:
            if repo.get("status") == "ready":
                return repo["graph"], repo
        return demo_graph, None

    # -- misc --------------------------------------------------------------

    @property
    def max_repos(self) -> int:
        return self._max_repos
