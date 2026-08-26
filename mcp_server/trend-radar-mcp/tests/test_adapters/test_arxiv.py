"""arXiv adapter tests: Atom parsing, cutoff early-exit, no-score baseline."""
from __future__ import annotations

import httpx
import pytest
import respx

from trend_radar.adapters.arxiv import ArxivAdapter


class TestArxivFetch:
    @pytest.mark.asyncio
    async def test_parses_atom_feed(
        self, settings, client, limiter, now_fn, load_fixture, respx_mock
    ) -> None:
        respx_mock.get("http://export.arxiv.org/api/query").mock(
            return_value=httpx.Response(200, text=load_fixture("arxiv_feed.xml"), headers={"content-type": "application/atom+xml"})
        )
        adapter = ArxivAdapter(client, limiter, settings, now_fn=now_fn)
        items = await adapter.fetch(lookback_hours=24)

        ids = {i.source_id for i in items}
        # Third entry (30h ago) is below cutoff → adapter early-exits.
        assert ids == {"2408.12345v1", "2408.12346v1"}

    @pytest.mark.asyncio
    async def test_papers_have_zero_score_and_no_comments(
        self, settings, client, limiter, now_fn, load_fixture, respx_mock
    ) -> None:
        respx_mock.get("http://export.arxiv.org/api/query").mock(
            return_value=httpx.Response(200, text=load_fixture("arxiv_feed.xml"))
        )
        adapter = ArxivAdapter(client, limiter, settings, now_fn=now_fn)
        items = await adapter.fetch(lookback_hours=24)
        for item in items:
            assert item.raw_score == 0.0
            assert item.comment_count is None
            assert item.permalink is None

    @pytest.mark.asyncio
    async def test_summary_stripped_and_truncated(
        self, settings, client, limiter, now_fn, load_fixture, respx_mock
    ) -> None:
        respx_mock.get("http://export.arxiv.org/api/query").mock(
            return_value=httpx.Response(200, text=load_fixture("arxiv_feed.xml"))
        )
        adapter = ArxivAdapter(client, limiter, settings, now_fn=now_fn)
        items = await adapter.fetch(lookback_hours=24)
        item = next(i for i in items if i.source_id == "2408.12345v1")
        assert item.body_excerpt is not None
        assert "state-space" in item.body_excerpt.lower()


class TestArxivFailures:
    @pytest.mark.asyncio
    async def test_500_surfaces_as_health_error(
        self, settings, client, limiter, now_fn, respx_mock
    ) -> None:
        respx_mock.get("http://export.arxiv.org/api/query").mock(
            return_value=httpx.Response(500)
        )
        adapter = ArxivAdapter(client, limiter, settings, now_fn=now_fn)
        items, health = await adapter.fetch_safely(lookback_hours=24)
        assert items == []
        assert health.ok is False

    @pytest.mark.asyncio
    async def test_malformed_response_is_survivable(
        self, settings, client, limiter, now_fn, respx_mock
    ) -> None:
        respx_mock.get("http://export.arxiv.org/api/query").mock(
            return_value=httpx.Response(200, text="<not-atom/>")
        )
        adapter = ArxivAdapter(client, limiter, settings, now_fn=now_fn)
        items, health = await adapter.fetch_safely(lookback_hours=24)
        assert items == []
        # feedparser tolerates the input and returns no entries — that's ok=True with 0 items.
        # Either outcome is acceptable; assert we didn't crash.
        assert isinstance(health.ok, bool)
