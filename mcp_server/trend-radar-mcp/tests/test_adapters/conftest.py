"""Shared adapter test fixtures.

Every adapter test gets:
- `now`: a fixed datetime, chosen to align with fixture timestamps
- `now_fn`: a callable returning it (adapter takes this via `now_fn=`)
- `client`: a real httpx.AsyncClient whose transport is a respx MockRouter
- `settings`: AppSettings with defaults (no creds — none required for HN/arXiv/HF)
- `limiter`: an instant-acquire TokenBucket (no real sleeps)
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from trend_radar.config import AppSettings
from trend_radar.http import TokenBucket

FIXTURES = Path(__file__).parent.parent / "fixtures"

# Aligned with the timestamps baked into the fixture files.
# 1787572800 == 2026-08-24T12:00:00Z. Items at *562000/*529600 are within 24h;
# *464800 (30h ago) falls outside.
FROZEN_NOW = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)


@pytest.fixture()
def now() -> datetime:
    return FROZEN_NOW


@pytest.fixture()
def now_fn(now: datetime) -> Callable[[], datetime]:
    return lambda: now


@pytest.fixture()
def load_fixture() -> Callable[[str], object]:
    def _load(name: str) -> object:
        path = FIXTURES / name
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            return json.loads(text)
        return text
    return _load


@pytest.fixture()
def settings() -> AppSettings:
    """Default settings — the conftest at tests/ already wipes env for us."""
    return AppSettings()


@pytest.fixture()
def limiter() -> TokenBucket:
    """A limiter that never actually paces (huge burst, tiny sleep)."""
    return TokenBucket(rate_per_sec=1000.0, burst=1000)


@pytest_asyncio.fixture()
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """A real AsyncClient. Tests attach respx via `respx_mock` and it intercepts."""
    async with httpx.AsyncClient(timeout=5.0) as c:
        yield c
