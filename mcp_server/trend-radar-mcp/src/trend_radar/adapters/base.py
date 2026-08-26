"""Adapter ABC + partial-failure wrapper.

Contract for every source: `fetch(lookback_hours) -> list[RawItem]`.
Any exception it raises is turned by `fetch_safely` into an `ok=False`
SourceHealth — one adapter's failure never propagates to the tool boundary.
"""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime
from typing import ClassVar

import httpx

from trend_radar.config import AppSettings
from trend_radar.errors import redact
from trend_radar.http import TokenBucket, now_utc
from trend_radar.models import RawItem, SourceHealth, SourceName

# Hard ceiling per adapter — a hanging upstream never blocks the whole run.
_FETCH_TIMEOUT_SECONDS: float = 60.0


class SourceAdapter(ABC):
    """Base class for the four ingest adapters."""

    name: ClassVar[SourceName]

    def __init__(
        self,
        client: httpx.AsyncClient,
        limiter: TokenBucket,
        settings: AppSettings,
        *,
        now_fn: Callable[[], datetime] = now_utc,
    ) -> None:
        self.client = client
        self.limiter = limiter
        self.settings = settings
        self._now = now_fn

    @abstractmethod
    async def fetch(self, lookback_hours: int) -> list[RawItem]:
        """Pull items from this source, filtered to the lookback window.

        May raise. Callers should invoke `fetch_safely()` instead of
        calling this directly, unless they want the raw exception.
        """
        raise NotImplementedError

    async def fetch_safely(self, lookback_hours: int) -> tuple[list[RawItem], SourceHealth]:
        """Wrap `fetch()` in try/except + timeout, return items and a health record."""
        started = time.monotonic()
        try:
            items = await asyncio.wait_for(self.fetch(lookback_hours), timeout=_FETCH_TIMEOUT_SECONDS)
            latency_ms = int((time.monotonic() - started) * 1000)
            return items, SourceHealth(
                source=self.name,
                ok=True,
                items_fetched=len(items),
                latency_ms=latency_ms,
            )
        except asyncio.TimeoutError:
            latency_ms = int((time.monotonic() - started) * 1000)
            return [], SourceHealth(
                source=self.name,
                ok=False,
                error=f"timeout after {_FETCH_TIMEOUT_SECONDS:.0f}s",
                latency_ms=latency_ms,
            )
        except Exception as exc:  # noqa: BLE001 — the whole point is to trap everything
            latency_ms = int((time.monotonic() - started) * 1000)
            return [], SourceHealth(
                source=self.name,
                ok=False,
                error=_short_error(exc),
                latency_ms=latency_ms,
            )


def _short_error(exc: BaseException) -> str:
    """One-line, redacted description of an exception. Never a stack trace."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code} from {exc.request.url.host}"
    if isinstance(exc, httpx.RequestError):
        return f"{type(exc).__name__}: {exc}".strip()
    return redact(f"{type(exc).__name__}: {exc}".strip())
