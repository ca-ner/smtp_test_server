"""Tiny dependency-free, thread-safe sliding-window rate limiter.

Keyed by an arbitrary string (e.g. a client IP). Suitable for a single
in-memory process; for multi-instance deployments use a shared store.
"""

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, max_events: int, window_seconds: int = 60):
        self.max_events = max_events
        self.window = window_seconds
        self._hits: dict = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        """Record an event for ``key``; return False if over the limit."""
        now = time.time()
        cutoff = now - self.window
        with self._lock:
            dq = self._hits[key]
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= self.max_events:
                return False
            dq.append(now)
            # opportunistic cleanup so idle keys don't accumulate
            if len(self._hits) > 10000:
                self._gc(cutoff)
            return True

    def _gc(self, cutoff: float):
        for k in [k for k, v in self._hits.items() if not v or v[-1] < cutoff]:
            self._hits.pop(k, None)
