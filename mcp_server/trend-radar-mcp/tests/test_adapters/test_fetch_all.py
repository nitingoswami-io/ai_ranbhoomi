"""Integration test: fetch_all runs concurrently and survives partial failure.

The core invariant of §1: 'one dead source never produces an empty result
set or an exception at the MCP boundary'. That's what this exercises.
"""
from __future__ import annotations

import httpx
import pytest

from trend_radar.adapters import build_adapters, fetch_all


class TestFetchAll:
    @pytest.mark.asyncio
    async def test_partial_failure_still_returns_healthy_sources(
        self, settings, client, now_fn, load_fixture, respx_mock
    ) -> None:
        # HN: 500 → ok=False; arXiv + HF: ok. Proves partial failure survives.
        respx_mock.get("https://hn.algolia.com/api/v1/search_by_date").mock(
            return_value=httpx.Response(500)
        )
        respx_mock.get("http://export.arxiv.org/api/query").mock(
            return_value=httpx.Response(200, text=load_fixture("arxiv_feed.xml"))
        )
        respx_mock.get("https://huggingface.co/api/daily_papers").mock(
            return_value=httpx.Response(200, json=load_fixture("hf_daily.json"))
        )

        adapters = build_adapters(settings, client)
        for a in adapters:
            a._now = now_fn  # noqa: SLF001 — inject test clock uniformly

        results = await fetch_all(adapters, lookback_hours=24)
        assert len(results) == 3
        by_source = {h.source: (items, h) for items, h in results}

        assert by_source["hackernews"][1].ok is False
        assert "500" in (by_source["hackernews"][1].error or "")

        assert by_source["arxiv"][1].ok is True
        assert by_source["arxiv"][1].items_fetched >= 1

        assert by_source["huggingface"][1].ok is True
        assert by_source["huggingface"][1].items_fetched >= 1

    @pytest.mark.asyncio
    async def test_never_raises_at_boundary(
        self, settings, client, now_fn, respx_mock
    ) -> None:
        # Break every source in a different way. fetch_all must still return normally.
        respx_mock.get("https://hn.algolia.com/api/v1/search_by_date").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        respx_mock.get("http://export.arxiv.org/api/query").mock(
            return_value=httpx.Response(502)
        )
        respx_mock.get("https://huggingface.co/api/daily_papers").mock(
            side_effect=httpx.ReadTimeout("timeout")
        )

        adapters = build_adapters(settings, client)
        for a in adapters:
            a._now = now_fn  # noqa: SLF001

        results = await fetch_all(adapters, lookback_hours=24)
        assert len(results) == 3
        assert all(not h.ok for _, h in results)
        # All errors are one-line strings, no tracebacks
        for _, h in results:
            assert h.error is not None
            assert "\n" not in h.error
