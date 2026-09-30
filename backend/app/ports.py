"""Ports: the interfaces the core depends on.

Services and the detection engine only know these protocols. Concrete implementations
(PBKDF2, HMAC tokens, SQLite, Gemini, ...) are chosen in one place, the container, and
injected. Swapping one, or faking it in a test, never touches the code that uses it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Protocol

Clock = Callable[[], datetime]


# --------------------------------------------------------------------------- security
class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...
    def verify(self, password: str, stored: str | None) -> bool: ...


@dataclass(frozen=True)
class TokenClaims:
    user_id: str
    session_id: str


class TokenService(Protocol):
    def issue(self, user_id: str, session_id: str) -> str: ...
    def verify(self, token: str) -> TokenClaims | None:
        """Return the claims of a valid, unexpired, untampered token, else None."""


@dataclass(frozen=True)
class Session:
    id: str              # the stored (hashed) session id
    user_id: str
    expires_at: str


class SessionStore(Protocol):
    """Server-side sessions, so a token can be revoked (logout, deactivated user)."""

    def create(self, session_id: str, user_id: str, created_at: str, expires_at: str) -> None: ...
    def get_active(self, session_id: str, now: str) -> Session | None: ...
    def revoke(self, session_id: str, at: str) -> None: ...


class LoginThrottle(Protocol):
    def blocked(self, *keys: str) -> bool: ...
    def fail(self, *keys: str) -> None: ...
    def reset(self, *keys: str) -> None: ...


class RateLimiter(Protocol):
    def allow(self, key: str) -> bool: ...


# --------------------------------------------------------------------------- detection
@dataclass(frozen=True)
class Verdict:
    contradicts: bool
    explanation: str | None = None


class ConflictJudge(Protocol):
    """Decides whether two claims about the same subject contradict each other."""

    name: str

    def judge(self, item_a: dict, claim_a: dict, item_b: dict, claim_b: dict) -> Verdict: ...


class ClaimExtractor(Protocol):
    """Turns a knowledge item into checkable claims."""

    def extract(self, item: dict, known_subjects: list[str]) -> list[dict]: ...
