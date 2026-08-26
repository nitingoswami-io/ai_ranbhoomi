"""Lexical fallback clustering tests: rapidfuzz threshold, representative selection."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from trend_radar.clustering import cluster_items
from trend_radar.config import AppSettings
from trend_radar.models import RawItem

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


def _item(source: str, source_id: str, title: str, *, score: float = 10.0) -> RawItem:
    return RawItem(
        source=source,  # type: ignore[arg-type]
        source_id=source_id,
        title=title,
        url=f"https://example.com/{source_id}",
        permalink=f"https://example.com/perma/{source_id}" if source != "arxiv" else None,
        raw_score=score,
        comment_count=None,
        created_at=NOW,
    )


@pytest.fixture()
def no_llm_settings() -> AppSettings:
    """Settings without ANTHROPIC_API_KEY → forces lexical path."""
    return AppSettings()  # conftest wipes env, so no key present


class TestLexicalCluster:
    @pytest.mark.asyncio
    async def test_empty_input(self, no_llm_settings: AppSettings) -> None:
        clusters, method = await cluster_items([], no_llm_settings)
        assert clusters == []
        assert method == "lexical"

    @pytest.mark.asyncio
    async def test_similar_titles_group_together(self, no_llm_settings: AppSettings) -> None:
        items = [
            _item("hackernews", "r1", "Claude Opus 5 crushes SWE-bench"),
            _item("hackernews", "h1", "Claude Opus 5 crushes SWE-bench benchmarks"),
            _item("hackernews", "r2", "New Mamba variant matches transformers"),
        ]
        clusters, method = await cluster_items(items, no_llm_settings)
        assert method == "lexical"
        assert len(clusters) == 2
        # Sort so assertions are deterministic
        by_size = sorted(clusters, key=lambda c: len(c.items), reverse=True)
        assert len(by_size[0].items) == 2
        assert len(by_size[1].items) == 1

    @pytest.mark.asyncio
    async def test_dissimilar_titles_stay_separate(self, no_llm_settings: AppSettings) -> None:
        items = [
            _item("hackernews", "r1", "Claude Opus 5 crushes SWE-bench"),
            _item("hackernews", "h1", "TensorRT-LLM release with speculative decoding"),
            _item("arxiv", "a1", "Efficient Attention via State-Space Duality"),
        ]
        clusters, _ = await cluster_items(items, no_llm_settings)
        assert len(clusters) == 3

    @pytest.mark.asyncio
    async def test_representative_is_highest_score_item(
        self, no_llm_settings: AppSettings
    ) -> None:
        items = [
            _item("hackernews", "low", "Claude Opus 5 released", score=1.0),
            _item("hackernews", "high", "Claude Opus 5 released by Anthropic today", score=1000.0),
        ]
        clusters, _ = await cluster_items(items, no_llm_settings)
        assert len(clusters) == 1
        assert clusters[0].topic.canonical_title == "Claude Opus 5 released by Anthropic today"

    @pytest.mark.asyncio
    async def test_canonical_title_capped_at_80(
        self, no_llm_settings: AppSettings
    ) -> None:
        long_title = "A really really really long title that goes on and on " * 3
        items = [_item("hackernews", "r1", long_title)]
        clusters, _ = await cluster_items(items, no_llm_settings)
        assert len(clusters[0].topic.canonical_title) <= 80

    @pytest.mark.asyncio
    async def test_lexical_leaves_entities_and_tags_empty(
        self, no_llm_settings: AppSettings
    ) -> None:
        items = [_item("hackernews", "r1", "Anthropic ships Claude Opus 5")]
        clusters, _ = await cluster_items(items, no_llm_settings)
        assert clusters[0].topic.entities == []
        assert clusters[0].topic.tags == []

    @pytest.mark.asyncio
    async def test_no_api_key_uses_lexical(self, no_llm_settings: AppSettings) -> None:
        # Sanity: confirm the fixture actually has no key set
        assert not no_llm_settings.has_anthropic_key()
        _, method = await cluster_items(
            [_item("hackernews", "r1", "test")], no_llm_settings
        )
        assert method == "lexical"
