"""Adapter registry + orchestration.

Callers:
- `build_adapters(settings, client)` constructs the three adapters with
  per-source rate limits.
- `fetch_all(adapters, lookback_hours)` runs them concurrently and returns
  (items, health) tuples in the same order as `adapters`. Never raises.
"""
from __future__ import annotations

import asyncio

import httpx

from trend_radar.adapters.arxiv import ArxivAdapter
from trend_radar.adapters.base import SourceAdapter
from trend_radar.adapters.hackernews import HackerNewsAdapter
from trend_radar.adapters.huggingface import HuggingFaceAdapter
from trend_radar.config import AppSettings
from trend_radar.http import TokenBucket
from trend_radar.models import RawItem, SourceHealth

__all__ = [
    "SourceAdapter",
    "HackerNewsAdapter",
    "ArxivAdapter",
    "HuggingFaceAdapter",
    "build_adapters",
    "fetch_all",
]


def build_adapters(settings: AppSettings, client: httpx.AsyncClient) -> list[SourceAdapter]:
    """Instantiate the three adapters with per-source rate limits chosen from public ToS."""
    return [
        HackerNewsAdapter(client, TokenBucket(rate_per_sec=1.0, burst=5), settings),
        ArxivAdapter(client, TokenBucket(rate_per_sec=0.34, burst=2), settings),
        HuggingFaceAdapter(client, TokenBucket(rate_per_sec=1.0, burst=5), settings),
    ]


async def fetch_all(
    adapters: list[SourceAdapter], lookback_hours: int
) -> list[tuple[list[RawItem], SourceHealth]]:
    """Fetch from every adapter concurrently. Order matches `adapters`."""
    return await asyncio.gather(*(a.fetch_safely(lookback_hours) for a in adapters))
