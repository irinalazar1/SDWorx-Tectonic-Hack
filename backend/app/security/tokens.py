"""Signed, expiring session tokens (implements ports.TokenService).

The token only carries a user id and a random session id; it is useless without the
matching server-side session, which can be revoked.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Callable

from ..ports import TokenClaims


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


class HmacTokenService:
    MAX_TOKEN_LENGTH = 1024

    def __init__(self, secret_key: str, ttl_seconds: int, clock: Callable[[], float] = time.time):
        if len(secret_key) < 32:
            raise ValueError("secret_key must be at least 32 characters")
        self._key = secret_key.encode()
        self._ttl = ttl_seconds
        self._clock = clock

    def _sign(self, body: str) -> str:
        return _b64(hmac.new(self._key, body.encode(), hashlib.sha256).digest())

    def issue(self, user_id: str, session_id: str) -> str:
        now = int(self._clock())
        payload = {"sub": user_id, "sid": session_id, "iat": now, "exp": now + self._ttl}
        body = _b64(json.dumps(payload, separators=(",", ":")).encode())
        return f"{body}.{self._sign(body)}"

    def verify(self, token: str) -> TokenClaims | None:
        if not token or len(token) > self.MAX_TOKEN_LENGTH:
            return None
        try:
            body, sig = token.split(".", 1)
            if not hmac.compare_digest(sig, self._sign(body)):
                return None
            payload = json.loads(_unb64(body))
            if not isinstance(payload, dict) or int(payload.get("exp", 0)) < self._clock():
                return None
            sub, sid = payload.get("sub"), payload.get("sid")
            if not isinstance(sub, str) or not isinstance(sid, str):
                return None
            return TokenClaims(user_id=sub, session_id=sid)
        except (ValueError, TypeError, json.JSONDecodeError):
            return None
