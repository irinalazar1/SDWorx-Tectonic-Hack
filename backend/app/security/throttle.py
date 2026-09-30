"""Login throttling and request rate limiting (implement ports.LoginThrottle / ports.RateLimiter)."""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable


class InMemoryLoginThrottle:
    """Blocks a username or client address after repeated failed logins.

    In-memory is enough for a single-process deployment; a shared store (e.g. Redis)
    can implement the same port without changing the auth service.
    """

    def __init__(self, max_failures: int = 5, window_seconds: int = 300,
                 clock: Callable[[], float] = time.time):
        self.max_failures = max_failures
        self.window = window_seconds
        self._clock = clock
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str) -> list[float]:
        now = self._clock()
        attempts = [t for t in self._failures.get(key, []) if now - t < self.window]
        if attempts:
            self._failures[key] = attempts
        else:
            self._failures.pop(key, None)  # keeps memory bounded
        return attempts

    def blocked(self, *keys: str) -> bool:
        with self._lock:
            return any(len(self._recent(k)) >= self.max_failures for k in keys)

    def fail(self, *keys: str) -> None:
        with self._lock:
            for k in keys:
                self._failures[k] = [*self._recent(k), self._clock()]

    def reset(self, *keys: str) -> None:
        with self._lock:
            for k in keys:
                self._failures.pop(k, None)


class SlidingWindowRateLimiter:
    """Allows at most `limit` requests per key within `window_seconds`."""

    MAX_KEYS = 50_000

    def __init__(self, limit: int, window_seconds: int, clock: Callable[[], float] = time.time):
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            if len(self._hits) > self.MAX_KEYS:  # bounded memory under address flooding
                self._hits.clear()
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True
