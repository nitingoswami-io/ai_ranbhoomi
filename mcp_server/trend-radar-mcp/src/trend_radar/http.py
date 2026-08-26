"""Shared HTTP client + token-bucket rate limiter + injectable clock.

Adapters import from here rather than instantiating their own httpx client
or reading `datetime.now()` directly. That lets tests wire in fake time
and a respx-mocked transport without touching adapter code.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

from trend_radar import __version__

_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)


def now_utc() -> datetime:
    """Timezone-aware UTC 'now'. Tests inject this via adapter's `now_fn`."""
    return datetime.now(UTC)


def create_http_client() -> httpx.AsyncClient:
    """One AsyncClient, shared across all adapters for a pipeline run."""
    return httpx.AsyncClient(
        timeout=_DEFAULT_TIMEOUT,
        headers={"User-Agent": f"trend-radar/{__version__}"},
        follow_redirects=True,
    )


class TokenBucket:
    """Async token bucket — leaky-bucket-style pacing.

    `rate_per_sec` tokens accumulate up to `burst`. Each `acquire()` costs
    one token; if the bucket is empty, the call sleeps until one is
    available. A single lock serializes acquires so concurrent callers
    are paced correctly rather than all draining in parallel.

    `time_fn` is injectable for tests — pass a fake clock instead of
    real sleep-and-wait loops.
    """

    def __init__(
        self,
        rate_per_sec: float,
        burst: int,
        *,
        time_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], object] | None = None,
    ) -> None:
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be > 0")
        if burst < 1:
            raise ValueError("burst must be >= 1")
        self.rate = float(rate_per_sec)
        self.capacity = float(burst)
        self._tokens = float(burst)
        self._time = time_fn
        self._sleep = sleep_fn or asyncio.sleep
        self._last: float | None = None
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = self._time()
            if self._last is None:
                self._last = now
            elapsed = now - self._last
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._last = now
            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self.rate
                await self._sleep(wait)
                # After sleep, one token is available; consume it.
                self._tokens = 0.0
                self._last = self._time()
            else:
                self._tokens -= 1.0
