"""Sliding-window per-user rate limiter for Discord command abuse protection."""
from __future__ import annotations
import time
from collections import defaultdict


class RateLimiter:
    """Allow at most *calls* actions per *period* seconds per user ID."""

    def __init__(self, calls: int, period: float):
        self._calls  = calls
        self._period = period
        self._history: dict[int, list[float]] = defaultdict(list)

    def check(self, user_id: int) -> tuple[bool, float]:
        """Return (allowed, retry_after_seconds).

        Thread-safe for single-threaded asyncio use — no locking needed.
        """
        now = time.monotonic()
        h   = [t for t in self._history[user_id] if now - t < self._period]
        self._history[user_id] = h
        if len(h) >= self._calls:
            return False, self._period - (now - h[0])
        h.append(now)
        return True, 0.0
