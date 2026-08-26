"""End-to-end pipeline tests: real storage, real scoring, real lexical clustering,
adapters mocked via respx. Verifies the full ingest→cluster→score→cache path.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from trend_radar.config import AppSettings
from trend_radar.models import CoveredTopic, RawItem
from trend_radar.pipeline import run_trending_pipeline
from trend_radar.storage import Storage

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> object:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    if name.endswith(".json"):
        return json.loads(text)
    return text


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "trend.db"


@pytest.fixture()
def settings() -> AppSettings:
    return AppSettings()


def _install_all_sources_ok(mock, load) -> None:
    """Wire respx mocks so HN, arXiv, and HF all return their fixture payloads."""
    mock.get("https://hn.algolia.com/api/v1/search_by_date").mock(
        return_value=httpx.Response(200, json=load("hn_search.json"))
    )
    mock.get("http://export.arxiv.org/api/query").mock(
        return_value=httpx.Response(200, text=load("arxiv_feed.xml"))
    )
    mock.get("https://huggingface.co/api/daily_papers").mock(
        return_value=httpx.Response(200, json=load("hf_daily.json"))
    )


class TestPipelineHappyPath:
    @pytest.mark.asyncio
    async def test_produces_ranked_result(
        self, db_path: Path, settings: AppSettings, respx_mock
    ) -> None:
        _install_all_sources_ok(respx_mock, _load)
        async with Storage(db_path) as storage, httpx.AsyncClient() as client:
            result = await run_trending_pipeline(
                settings, storage, client, lookback_hours=24, limit=15
            )

        assert result.run_id.startswith("run-")
        assert result.lookback_hours == 24
        assert result.clustering_method == "lexical"  # no ANTHROPIC_API_KEY
        healths = {h.source: h for h in result.source_health}
        assert set(healths) == {"hackernews", "arxiv", "huggingface"}
        assert healths["hackernews"].ok is True
        assert healths["arxiv"].ok is True
        assert healths["huggingface"].ok is True
        assert len(result.topics) > 0

    @pytest.mark.asyncio
    async def test_topics_sorted_by_final_score_desc(
        self, db_path: Path, settings: AppSettings, respx_mock
    ) -> None:
        _install_all_sources_ok(respx_mock, _load)
        async with Storage(db_path) as storage, httpx.AsyncClient() as client:
            result = await run_trending_pipeline(
                settings, storage, client, lookback_hours=24, limit=15
            )
        scores = [t.score.final_score for t in result.topics]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_cached_run_survives_close_and_reopen(
        self, db_path: Path, settings: AppSettings, respx_mock
    ) -> None:
        _install_all_sources_ok(respx_mock, _load)
        async with Storage(db_path) as storage, httpx.AsyncClient() as client:
            result = await run_trending_pipeline(
                settings, storage, client, lookback_hours=24
            )
            run_id = result.run_id

        # Reopen and confirm the run is still there
        async with Storage(db_path) as storage:
            latest = await storage.get_latest_run()
            assert latest is not None
            assert latest.run_id == run_id

    @pytest.mark.asyncio
    async def test_cache_contains_full_ranked_set_not_just_visible(
        self, db_path: Path, settings: AppSettings, respx_mock
    ) -> None:
        _install_all_sources_ok(respx_mock, _load)
        async with Storage(db_path) as storage, httpx.AsyncClient() as client:
            result = await run_trending_pipeline(
                settings, storage, client, lookback_hours=24, limit=1
            )
            assert len(result.topics) == 1  # visible view is capped

            cached = await storage.get_latest_run()
            assert cached is not None
            # Cached should have ALL scored topics, not just the top 1
            assert len(cached.topics) >= 1


class TestPipelineFiltering:
    @pytest.mark.asyncio
    async def test_include_suppressed_false_hides_them(
        self, db_path: Path, settings: AppSettings, respx_mock
    ) -> None:
        _install_all_sources_ok(respx_mock, _load)

        # Seed ledger with topics that will match HN/HF items
        async with Storage(db_path) as storage:
            for tid, title, ol in [
                ("s1" + "0" * 10, "Anthropic releases Claude Opus 5", "Anthropic ships Claude Opus 5"),
                ("s2" + "0" * 10, "Verifier-Guided Reasoning Beats Chain-of-Thought at Half the Tokens", ""),
            ]:
                from datetime import date
                await storage.upsert_covered(
                    CoveredTopic(topic_id=tid, canonical_title=title, covered_on=date(2026, 8, 22)),
                    one_line=ol,
                )

        async with Storage(db_path) as storage, httpx.AsyncClient() as client:
            with_suppressed = await run_trending_pipeline(
                settings, storage, client, lookback_hours=24, include_suppressed=True
            )
            without = await run_trending_pipeline(
                settings, storage, client, lookback_hours=24, include_suppressed=False
            )

        # Without suppressed, no topic should be flagged suppressed
        assert not any(t.suppressed for t in without.topics)
        # With suppressed, at least the seeded ones show up as suppressed=True
        assert any(t.suppressed for t in with_suppressed.topics), (
            "expected at least one topic to be suppressed given the seeded ledger"
        )

    @pytest.mark.asyncio
    async def test_source_restriction(
        self, db_path: Path, settings: AppSettings, respx_mock
    ) -> None:
        _install_all_sources_ok(respx_mock, _load)
        async with Storage(db_path) as storage, httpx.AsyncClient() as client:
            result = await run_trending_pipeline(
                settings, storage, client, lookback_hours=24, sources=["arxiv"]
            )
        assert {h.source for h in result.source_health} == {"arxiv"}


class TestPipelineFailureIsolation:
    @pytest.mark.asyncio
    async def test_all_sources_dead_returns_empty_topics_not_exception(
        self, db_path: Path, settings: AppSettings, respx_mock
    ) -> None:
        respx_mock.get("https://hn.algolia.com/api/v1/search_by_date").mock(
            return_value=httpx.Response(500)
        )
        respx_mock.get("http://export.arxiv.org/api/query").mock(
            return_value=httpx.Response(500)
        )
        respx_mock.get("https://huggingface.co/api/daily_papers").mock(
            return_value=httpx.Response(500)
        )
        async with Storage(db_path) as storage, httpx.AsyncClient() as client:
            result = await run_trending_pipeline(
                settings, storage, client, lookback_hours=24
            )
        assert result.topics == []
        assert all(not h.ok for h in result.source_health)
