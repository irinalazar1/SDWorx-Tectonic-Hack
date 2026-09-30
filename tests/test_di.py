"""Loose coupling in practice: each component is tested in isolation or swapped via injection,
with no HTTP, database or network unless the test is about that."""
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app.api.deps import get_current_user
from backend.app.container import build_container
from backend.app.detection import DetectionEngine
from backend.app.domain import AuthenticationFailed, Forbidden, NotFound, Snapshot, TooManyAttempts, User
from backend.app.main import create_app
from backend.app.ports import Session, Verdict
from backend.app.security import (AccessPolicy, AuthService, HmacTokenService, InMemoryLoginThrottle,
                                  SlidingWindowRateLimiter)
from backend.app.settings import load_settings
from tests.conftest import PASSWORD, test_settings

KEY = "k" * 40

OWNER_BE = User("u1", "Owner", "owner", "t", ("BE",))
CONSULTANT_BE = User("u2", "Consultant", "consultant", "t", ("BE",))


class FakeClock:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


# ---------------------------------------------------------------- security, in isolation
def test_policy_rules():
    policy = AccessPolicy()
    assert policy.can_view(OWNER_BE, "BE") and not policy.can_view(OWNER_BE, "NL")
    with pytest.raises(NotFound):
        policy.ensure_can_view(OWNER_BE, "NL")
    with pytest.raises(Forbidden):
        policy.ensure_can_resolve(CONSULTANT_BE, "BE", ["BE"])
    with pytest.raises(Forbidden):
        policy.ensure_can_resolve(OWNER_BE, "BE", ["BE", "NL"])
    policy.ensure_can_resolve(OWNER_BE, "BE", ["BE"])  # allowed


def test_token_expiry_uses_injected_clock():
    clock = FakeClock()
    tokens = HmacTokenService(KEY, ttl_seconds=60, clock=clock)
    token = tokens.issue("u1", "sid")
    assert tokens.verify(token).user_id == "u1" and tokens.verify(token).session_id == "sid"
    clock.t += 61
    assert tokens.verify(token) is None
    assert HmacTokenService("o" * 40, 60, clock).verify(tokens.issue("u1", "sid")) is None


def test_short_secrets_rejected():
    with pytest.raises(ValueError):
        HmacTokenService("short", 60)
    with pytest.raises(ValueError):
        test_settings(secret_key="short")


def test_rate_limiter_window_uses_injected_clock():
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=10, clock=clock)
    assert limiter.allow("a") and limiter.allow("a") and not limiter.allow("a")
    assert limiter.allow("b")
    clock.t += 10
    assert limiter.allow("a")


def test_throttle_window_uses_injected_clock():
    clock = FakeClock()
    throttle = InMemoryLoginThrottle(max_failures=2, window_seconds=10, clock=clock)
    throttle.fail("k"); throttle.fail("k")
    assert throttle.blocked("k")
    clock.t += 11
    assert not throttle.blocked("k")


class FakeUsers:
    def __init__(self, user: User, stored: str):
        self.user, self.stored = user, stored

    def get_active(self, user_id):
        return self.user if user_id == self.user.id else None

    def password_hash(self, user_id):
        return self.stored if user_id == self.user.id else None


class FakeSessions:
    def __init__(self):
        self.rows = {}

    def create(self, session_id, user_id, created_at, expires_at):
        self.rows[session_id] = Session(session_id, user_id, expires_at)

    def get_active(self, session_id, now):
        return self.rows.get(session_id)

    def revoke(self, session_id, at):
        self.rows.pop(session_id, None)


class FakeAudit:
    def __init__(self):
        self.events = []

    def log(self, at, actor, action, target, detail=""):
        self.events.append(action)


class FakeUow:
    def __init__(self, users):
        self.users, self.sessions, self.audit = users, FakeSessions(), FakeAudit()

    def commit(self):
        pass


class CountingHasher:
    """Plain-text hasher that records every verify call."""
    def __init__(self): self.verified = 0
    def hash(self, password): return f"plain:{password}"
    def verify(self, password, stored):
        self.verified += 1
        return stored == f"plain:{password}"


def utc():
    return datetime(2026, 9, 30, tzinfo=timezone.utc)


def _auth(hasher, max_failures=5):
    return AuthService(hasher, HmacTokenService(KEY, 60), InMemoryLoginThrottle(max_failures=max_failures),
                       clock=utc, csrf_key=KEY, session_ttl_seconds=60)


def test_auth_service_with_fake_collaborators():
    auth = _auth(CountingHasher(), max_failures=1)
    uow = FakeUow(FakeUsers(OWNER_BE, "plain:pw"))
    result = auth.login(uow, "u1", "pw", "1.2.3.4")
    principal = auth.authenticate(uow, result.token)
    assert principal.user == OWNER_BE and auth.csrf_valid(principal, result.csrf_token)
    auth.logout(uow, principal)
    with pytest.raises(AuthenticationFailed):
        auth.authenticate(uow, result.token)  # revoked
    with pytest.raises(AuthenticationFailed):
        auth.login(uow, "u1", "wrong", "5.6.7.8")
    with pytest.raises(TooManyAttempts):
        auth.login(uow, "u1", "pw", "5.6.7.8")
    assert uow.audit.events == ["auth.login", "auth.logout", "auth.failed", "auth.throttled"]


def test_unknown_user_still_costs_a_hash_check():
    """No shortcut for unknown usernames, so timing does not reveal which accounts exist."""
    hasher = CountingHasher()
    auth = _auth(hasher)
    uow = FakeUow(FakeUsers(OWNER_BE, "plain:pw"))
    with pytest.raises(AuthenticationFailed):
        auth.login(uow, "does-not-exist", "pw", "1.1.1.1")
    assert hasher.verified == 1


# ---------------------------------------------------------------- detection, in isolation
def _snapshot() -> Snapshot:
    item = lambda i, v: {"id": i, "title": i, "version": 1, "state": "active", "state_changed_at": None,
                         "owner_id": "o", "country": "BE", "topic": "leave", "created_at": "2026-01-01",
                         "review_by": "2027-01-01", "supersedes_id": None, "replaced_by_id": None,
                         "body": f"policy text {i} {v}"}
    claim = lambda v: [{"subject": "x", "value": v, "text": f"says {v}"}]
    return Snapshot(items={"A": item("A", "yes"), "B": item("B", "no")}, claims={"A": claim("yes"), "B": claim("no")},
                    users={"o": {"id": "o", "name": "O", "active": 1, "left_at": None}},
                    topics=[{"id": "leave", "keywords": "leave"}], chats=[], usage=[], as_of=date(2026, 6, 1))


class NeverContradicts:
    name = "fake"

    def judge(self, *args):
        return Verdict(False)


class AlwaysContradicts:
    name = "fake"

    def judge(self, *args):
        return Verdict(True, "Injected explanation")


def test_engine_uses_injected_judge():
    assert [c["type"] for c in DetectionEngine(NeverContradicts()).detect(_snapshot())] == []
    found = DetectionEngine(AlwaysContradicts()).detect(_snapshot())
    assert [c["type"] for c in found] == ["conflict"]
    assert found[0]["explanation"] == "Injected explanation"


# ---------------------------------------------------------------- whole app with swapped parts
class AlwaysBlocked:
    def blocked(self, *keys): return True
    def fail(self, *keys): pass
    def reset(self, *keys): pass


def test_app_uses_injected_throttle(container):
    app = create_app(build_container(container.settings, throttle=AlwaysBlocked()))
    res = TestClient(app).post("/api/login", json={"username": "u-an", "password": PASSWORD})
    assert res.status_code == 429


def test_dependency_override_of_current_user(container):
    app = create_app(container)
    app.dependency_overrides[get_current_user] = lambda: User("u-an", "An", "owner", "t", ("NL",))
    issues = TestClient(app).get("/api/map").json()["issues"]
    assert issues and all(i["country"] == "NL" for i in issues)


def test_settings_have_no_hidden_globals():
    assert load_settings(env_file=None).db_path.name.endswith(".db")
