"""HN Algolia adapter tests: multi-query fan-out, dedup, Ask HN URL fallback."""
from __future__ import annotations

import httpx
import pytest
import respx

from trend_radar.adapters.hackernews import AI_QUERIES, HackerNewsAdapter


class TestHackerNewsFetch:
    @pytest.mark.asyncio
    async def test_dedups_across_queries(
        self, settings, client, limiter, now_fn, load_fixture, respx_mock
    ) -> None:
        payload = load_fixture("hn_search.json")
        # Every AI_QUERIES search returns the same fixture — perfect for
        # exercising dedup: 5 responses × 3 hits each = 15 raw, unique = 3.
        respx_mock.get("https://hn.algolia.com/api/v1/search_by_date").mock(
            return_value=httpx.Response(200, json=payload)
        )
        adapter = HackerNewsAdapter(client, limiter, settings, now_fn=now_fn)

        items = await adapter.fetch(lookback_hours=24)
        assert len(items) == 3, [i.source_id for i in items]
        assert {i.source_id for i in items} == {"41000001", "41000002", "41000003"}

    @pytest.mark.asyncio
    async def test_ask_hn_url_falls_back_to_discussion_link(
        self, settings, client, limiter, now_fn, load_fixture, respx_mock
    ) -> None:
        respx_mock.get("https://hn.algolia.com/api/v1/search_by_date").mock(
            return_value=httpx.Response(200, json=load_fixture("hn_search.json"))
        )
        adapter = HackerNewsAdapter(client, limiter, settings, now_fn=now_fn)
        items = await adapter.fetch(lookback_hours=24)

        ask_hn = next(i for i in items if i.source_id == "41000003")
        assert str(ask_hn.url) == "https://news.ycombinator.com/item?id=41000003"
        assert str(ask_hn.permalink) == "https://news.ycombinator.com/item?id=41000003"

    @pytest.mark.asyncio
    async def test_all_five_queries_are_issued(
        self, settings, client, limiter, now_fn, load_fixture, respx_mock
    ) -> None:
        route = respx_mock.get("https://hn.algolia.com/api/v1/search_by_date").mock(
            return_value=httpx.Response(200, json=load_fixture("hn_search.json"))
        )
        adapter = HackerNewsAdapter(client, limiter, settings, now_fn=now_fn)
        await adapter.fetch(lookback_hours=24)
        assert route.call_count == len(AI_QUERIES)


class TestHackerNewsFailures:
    @pytest.mark.asyncio
    async def test_rate_limited_surfaces_as_health_error(
        self, settings, client, limiter, now_fn, respx_mock
    ) -> None:
        respx_mock.get("https://hn.algolia.com/api/v1/search_by_date").mock(
            return_value=httpx.Response(429, text="slow down")
        )
        adapter = HackerNewsAdapter(client, limiter, settings, now_fn=now_fn)
        items, health = await adapter.fetch_safely(lookback_hours=24)
        assert items == []
        assert health.ok is False
        assert "429" in (health.error or "")

    @pytest.mark.asyncio
    async def test_500_surfaces_as_health_error(
        self, settings, client, limiter, now_fn, respx_mock
    ) -> None:
        respx_mock.get("https://hn.algolia.com/api/v1/search_by_date").mock(
            return_value=httpx.Response(500)
        )
        adapter = HackerNewsAdapter(client, limiter, settings, now_fn=now_fn)
        items, health = await adapter.fetch_safely(lookback_hours=24)
        assert items == []
        assert health.ok is False
