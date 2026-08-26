"""HF Daily Papers adapter tests: date pagination, 404 tolerance, cutoff."""
from __future__ import annotations

import httpx
import pytest
import respx

from trend_radar.adapters.huggingface import HuggingFaceAdapter


class TestHuggingFaceFetch:
    @pytest.mark.asyncio
    async def test_single_day_fetch(
        self, settings, client, limiter, now_fn, load_fixture, respx_mock
    ) -> None:
        respx_mock.get("https://huggingface.co/api/daily_papers").mock(
            return_value=httpx.Response(200, json=load_fixture("hf_daily.json"))
        )
        adapter = HuggingFaceAdapter(client, limiter, settings, now_fn=now_fn)
        items = await adapter.fetch(lookback_hours=24)

        # Filter uses submittedOnDailyAt (HF curators' feature date), NOT the
        # paper's original publication date. Third entry has submittedOnDailyAt
        # 30h before now → filtered.
        ids = {i.source_id for i in items}
        assert ids == {"2408.55555", "2408.66666"}

    @pytest.mark.asyncio
    async def test_prefers_submitted_on_daily_at_over_published_at(
        self, settings, client, limiter, now_fn, respx_mock
    ) -> None:
        """A paper published days ago but featured on today's HF Daily should be included."""
        payload = [{
            "publishedAt": "2026-08-20T00:00:00.000Z",           # 4 days ago (ignored)
            "paper": {
                "id": "old.paper.new.feature",
                "title": "Old paper, newly featured",
                "summary": "Published days ago but HF picked it up on today's daily list.",
                "upvotes": 42,
                "publishedAt": "2026-08-20T00:00:00.000Z",       # also ignored
                "submittedOnDailyAt": "2026-08-24T06:00:00.000Z",  # 6h before NOW → within 24h
            },
        }]
        respx_mock.get("https://huggingface.co/api/daily_papers").mock(
            return_value=httpx.Response(200, json=payload)
        )
        adapter = HuggingFaceAdapter(client, limiter, settings, now_fn=now_fn)
        items = await adapter.fetch(lookback_hours=24)
        assert len(items) == 1
        assert items[0].source_id == "old.paper.new.feature"

    @pytest.mark.asyncio
    async def test_falls_back_to_published_at_when_submitted_missing(
        self, settings, client, limiter, now_fn, respx_mock
    ) -> None:
        """Older response shapes (no submittedOnDailyAt) still work via fallback."""
        payload = [{
            "publishedAt": "2026-08-24T06:00:00.000Z",  # 6h before NOW → within 24h
            "paper": {
                "id": "legacy.shape",
                "title": "Legacy response with only publishedAt",
                "summary": "No submittedOnDailyAt field.",
                "upvotes": 10,
                # deliberately no submittedOnDailyAt
            },
        }]
        respx_mock.get("https://huggingface.co/api/daily_papers").mock(
            return_value=httpx.Response(200, json=payload)
        )
        adapter = HuggingFaceAdapter(client, limiter, settings, now_fn=now_fn)
        items = await adapter.fetch(lookback_hours=24)
        assert len(items) == 1
        assert items[0].source_id == "legacy.shape"

    @pytest.mark.asyncio
    async def test_paper_fields_populated(
        self, settings, client, limiter, now_fn, load_fixture, respx_mock
    ) -> None:
        respx_mock.get("https://huggingface.co/api/daily_papers").mock(
            return_value=httpx.Response(200, json=load_fixture("hf_daily.json"))
        )
        adapter = HuggingFaceAdapter(client, limiter, settings, now_fn=now_fn)
        items = await adapter.fetch(lookback_hours=24)
        top = next(i for i in items if i.source_id == "2408.55555")
        assert top.title.startswith("Verifier-Guided Reasoning")
        assert top.raw_score == 128.0
        assert str(top.url) == "https://huggingface.co/papers/2408.55555"
        assert top.permalink is None

    @pytest.mark.asyncio
    async def test_404_is_treated_as_empty_day(
        self, settings, client, limiter, now_fn, respx_mock
    ) -> None:
        respx_mock.get("https://huggingface.co/api/daily_papers").mock(
            return_value=httpx.Response(404, json={"error": "not found"})
        )
        adapter = HuggingFaceAdapter(client, limiter, settings, now_fn=now_fn)
        items, health = await adapter.fetch_safely(lookback_hours=24)
        assert items == []
        assert health.ok is True  # 404 = "no papers that day" is not a failure

    @pytest.mark.asyncio
    async def test_multi_day_lookback_fetches_multiple_dates(
        self, settings, client, limiter, now_fn, load_fixture, respx_mock
    ) -> None:
        route = respx_mock.get("https://huggingface.co/api/daily_papers").mock(
            return_value=httpx.Response(200, json=load_fixture("hf_daily.json"))
        )
        adapter = HuggingFaceAdapter(client, limiter, settings, now_fn=now_fn)
        await adapter.fetch(lookback_hours=72)  # 3 days
        assert route.call_count == 3


class TestHuggingFaceFailures:
    @pytest.mark.asyncio
    async def test_500_surfaces_as_health_error(
        self, settings, client, limiter, now_fn, respx_mock
    ) -> None:
        respx_mock.get("https://huggingface.co/api/daily_papers").mock(
            return_value=httpx.Response(500)
        )
        adapter = HuggingFaceAdapter(client, limiter, settings, now_fn=now_fn)
        items, health = await adapter.fetch_safely(lookback_hours=24)
        assert items == []
        assert health.ok is False
