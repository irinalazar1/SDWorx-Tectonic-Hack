"""Authentication helpers: password hashing, signed session tokens, login throttling."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time

from . import config

_PBKDF2_ITERATIONS = 240_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        _, iterations, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def issue_token(user_id: str) -> str:
    payload = {"sub": user_id, "exp": int(time.time()) + config.TOKEN_TTL_SECONDS,
               "nonce": secrets.token_hex(8)}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(config.SECRET_KEY.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def read_token(token: str) -> str | None:
    """Return the user id for a valid, unexpired token, else None."""
    try:
        body, sig = token.split(".", 1)
        expected = _b64(hmac.new(config.SECRET_KEY.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_unb64(body))
        if int(payload.get("exp", 0)) < time.time():
            return None
        sub = payload.get("sub")
        return sub if isinstance(sub, str) else None
    except (ValueError, json.JSONDecodeError):
        return None


class LoginThrottle:
    """Blocks a username or client address after repeated failed logins."""

    def __init__(self, max_failures: int = 5, window_seconds: int = 300):
        self.max_failures = max_failures
        self.window = window_seconds
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str) -> list[float]:
        now = time.time()
        attempts = [t for t in self._failures.get(key, []) if now - t < self.window]
        self._failures[key] = attempts
        return attempts

    def blocked(self, *keys: str) -> bool:
        with self._lock:
            return any(len(self._recent(k)) >= self.max_failures for k in keys)

    def fail(self, *keys: str) -> None:
        with self._lock:
            for k in keys:
                self._recent(k).append(time.time())

    def reset(self, *keys: str) -> None:
        with self._lock:
            for k in keys:
                self._failures.pop(k, None)


throttle = LoginThrottle()
