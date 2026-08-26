"""Contract tests on the data models.

These are the API surface for the downstream writer agent. If any of these
assertions fails, that's a breaking change to consumers — flag it in the
PR description.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from trend_radar.models import (
    NormalizedTopic,
    NoveltyResult,
    RankedTopic,
    RawItem,
    ScoreBreakdown,
    ScoredItem,
    compute_topic_id,
)


def _raw_item(**overrides: object) -> RawItem:
    base = dict(
        source="hackernews",
        source_id="abc123",
        title="Claude Opus 5 announced",
        url="https://example.com/post",
        permalink="https://news.ycombinator.com/item?id=abc123",
        raw_score=42.0,
        comment_count=17,
        created_at=datetime(2026, 8, 24, 12, 0, tzinfo=UTC),
        body_excerpt=None,
    )
    base.update(overrides)  # type: ignore[arg-type]
    return RawItem(**base)  # type: ignore[arg-type]


class TestRawItem:
    def test_happy_path(self) -> None:
        item = _raw_item()
        assert item.source == "hackernews"
        assert item.raw_score == 42.0

    def test_frozen(self) -> None:
        item = _raw_item()
        with pytest.raises(ValidationError):
            item.raw_score = 100.0  # type: ignore[misc]

    def test_rejects_invalid_source(self) -> None:
        with pytest.raises(ValidationError):
            _raw_item(source="twitter")

    def test_reddit_is_no_longer_a_valid_source(self) -> None:
        # Reddit was dropped when their API policy stopped allowing this use case.
        with pytest.raises(ValidationError):
            _raw_item(source="reddit")

    def test_rejects_negative_score(self) -> None:
        with pytest.raises(ValidationError):
            _raw_item(raw_score=-1.0)

    def test_body_excerpt_length_cap(self) -> None:
        with pytest.raises(ValidationError):
            _raw_item(body_excerpt="x" * 501)


class TestNormalizedTopic:
    def test_title_length_cap(self) -> None:
        with pytest.raises(ValidationError):
            NormalizedTopic(canonical_title="x" * 81, one_line="short", entities=[], tags=[])

    def test_one_line_length_cap(self) -> None:
        with pytest.raises(ValidationError):
            NormalizedTopic(canonical_title="ok", one_line="x" * 161, entities=[], tags=[])

    def test_tags_capped_at_five(self) -> None:
        with pytest.raises(ValidationError):
            NormalizedTopic(
                canonical_title="ok",
                one_line="ok",
                entities=[],
                tags=["a", "b", "c", "d", "e", "f"],
            )


class TestScoring:
    def test_percentile_bounds(self) -> None:
        with pytest.raises(ValidationError):
            ScoreBreakdown(
                velocity=1.0,
                source_percentile=1.5,
                corroboration_bonus=0.0,
                novelty_multiplier=1.0,
                final_score=1.0,
                explanation="x",
            )

    def test_corroboration_capped(self) -> None:
        with pytest.raises(ValidationError):
            ScoreBreakdown(
                velocity=1.0,
                source_percentile=0.5,
                corroboration_bonus=0.35,  # > 0.30 cap (3 sources max)
                novelty_multiplier=1.0,
                final_score=1.0,
                explanation="x",
            )

    def test_ranked_topic_distinct_sources_bounds(self) -> None:
        topic = NormalizedTopic(canonical_title="t", one_line="o", entities=[], tags=[])
        score = ScoreBreakdown(
            velocity=1.0, source_percentile=0.5, corroboration_bonus=0.0,
            novelty_multiplier=1.0, final_score=0.5, explanation="x",
        )
        scored = ScoredItem(item=_raw_item(), velocity=1.0, source_percentile=0.5)
        with pytest.raises(ValidationError):
            RankedTopic(
                topic_id="a" * 12, topic=topic, items=[scored],
                distinct_sources=4, score=score,  # 3 is the new max
            )


class TestNoveltyAndHelpers:
    def test_novelty_similarity_bounds(self) -> None:
        with pytest.raises(ValidationError):
            NoveltyResult(is_novel=True, max_similarity=1.5)

    def test_novelty_optional_closest_match(self) -> None:
        assert NoveltyResult(is_novel=True, max_similarity=0.1).closest_match_date is None

    def test_topic_id_deterministic(self) -> None:
        assert compute_topic_id("Claude Opus 5") == compute_topic_id("  Claude Opus 5  ")
        assert compute_topic_id("Claude Opus 5") == compute_topic_id("claude opus 5")
        assert compute_topic_id("Claude Opus 5") != compute_topic_id("Claude Opus 6")

    def test_topic_id_length(self) -> None:
        assert len(compute_topic_id("anything at all")) == 12

    def test_covered_topic_date(self) -> None:
        from trend_radar.models import CoveredTopic
        c = CoveredTopic(topic_id="a" * 12, canonical_title="t", covered_on=date(2026, 1, 1))
        assert c.post_url is None
