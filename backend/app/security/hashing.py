"""Password hashing (implements ports.PasswordHasher)."""
from __future__ import annotations

import hashlib
import hmac
import secrets

MAX_PASSWORD_BYTES = 1024  # bounds the cost of hashing attacker-supplied input


class Pbkdf2PasswordHasher:
    def __init__(self, iterations: int = 600_000):
        self.iterations = iterations

    def hash(self, password: str) -> str:
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode()[:MAX_PASSWORD_BYTES], salt, self.iterations)
        return f"pbkdf2_sha256${self.iterations}${salt.hex()}${digest.hex()}"

    def verify(self, password: str, stored: str | None) -> bool:
        if not stored:
            return False
        try:
            scheme, iterations, salt_hex, digest_hex = stored.split("$")
            if scheme != "pbkdf2_sha256":
                return False
            digest = hashlib.pbkdf2_hmac("sha256", password.encode()[:MAX_PASSWORD_BYTES],
                                         bytes.fromhex(salt_hex), int(iterations))
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(digest.hex(), digest_hex)
