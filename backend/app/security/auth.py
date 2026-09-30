"""Authentication use cases. Depends only on ports, never on concrete implementations.

* Sessions live server-side (hashed ids), so logout and deactivation take effect immediately.
* The CSRF token is derived from the session with a keyed HMAC: nothing extra to store,
  and it can be re-issued to the page after a reload.
* Unknown usernames cost the same as wrong passwords (no user enumeration by timing).
* Every sign-in, failure and sign-out is written to the audit log.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta

from ..domain import AuthenticationFailed, TooManyAttempts, User
from ..ports import Clock, LoginThrottle, PasswordHasher, Session, TokenService

MAX_USERNAME_LENGTH = 64


def _hash_id(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class LoginResult:
    token: str
    csrf_token: str
    user: User


@dataclass(frozen=True)
class Principal:
    user: User
    session: Session
    csrf_token: str


class AuthService:
    def __init__(self, hasher: PasswordHasher, tokens: TokenService, throttle: LoginThrottle,
                 clock: Clock, csrf_key: str, session_ttl_seconds: int):
        self.hasher = hasher
        self.tokens = tokens
        self.throttle = throttle
        self.clock = clock
        self._csrf_key = hashlib.sha256(b"csrf:" + csrf_key.encode()).digest()
        self._ttl = timedelta(seconds=session_ttl_seconds)
        # Verified against when the username does not exist, so both paths cost the same.
        self._dummy_hash = hasher.hash(secrets.token_urlsafe(16))

    def _now(self) -> str:
        return self.clock().replace(microsecond=0).isoformat()

    def csrf_token_for(self, raw_session_id: str) -> str:
        return hmac.new(self._csrf_key, raw_session_id.encode(), hashlib.sha256).hexdigest()

    # ------------------------------------------------------------------ login / logout
    def login(self, uow, username: str, password: str, client: str) -> LoginResult:
        username = username.strip()[:MAX_USERNAME_LENGTH]
        keys = (f"user:{username.lower()}", f"ip:{client}")
        if self.throttle.blocked(*keys):
            uow.audit.log(self._now(), None, "auth.throttled", username, f"client {client}")
            uow.commit()  # keep the audit entry even though the request fails
            raise TooManyAttempts("Too many attempts. Try again in a few minutes.")

        user = uow.users.get_active(username)
        stored = uow.users.password_hash(username) if user else None
        password_ok = self.hasher.verify(password, stored or self._dummy_hash)
        if user is None or stored is None or not password_ok:
            self.throttle.fail(*keys)
            uow.audit.log(self._now(), None, "auth.failed", username, "invalid credentials")
            uow.commit()  # keep the audit entry even though the request fails
            raise AuthenticationFailed("Invalid username or password")

        self.throttle.reset(*keys)
        raw_session_id = secrets.token_urlsafe(32)
        now = self.clock().replace(microsecond=0)
        uow.sessions.create(_hash_id(raw_session_id), user.id, now.isoformat(), (now + self._ttl).isoformat())
        uow.audit.log(now.isoformat(), user.id, "auth.login", user.id, "")
        return LoginResult(self.tokens.issue(user.id, raw_session_id), self.csrf_token_for(raw_session_id), user)

    def logout(self, uow, principal: Principal) -> None:
        uow.sessions.revoke(principal.session.id, self._now())
        uow.audit.log(self._now(), principal.user.id, "auth.logout", principal.user.id, "")

    # ------------------------------------------------------------------ per request
    def authenticate(self, uow, token: str | None) -> Principal:
        claims = self.tokens.verify(token) if token else None
        if claims is None:
            raise AuthenticationFailed("Not signed in")
        session = uow.sessions.get_active(_hash_id(claims.session_id), self._now())
        if session is None or session.user_id != claims.user_id:
            raise AuthenticationFailed("Not signed in")
        user = uow.users.get_active(claims.user_id)
        if user is None:  # deactivated since sign-in
            uow.sessions.revoke(session.id, self._now())
            raise AuthenticationFailed("Not signed in")
        return Principal(user, session, self.csrf_token_for(claims.session_id))

    @staticmethod
    def csrf_valid(principal: Principal, presented: str | None) -> bool:
        return bool(presented) and hmac.compare_digest(presented, principal.csrf_token)
