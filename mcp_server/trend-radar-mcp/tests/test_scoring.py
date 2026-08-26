"""Scoring engine tests — exact-value assertions against hand-computed expectations.

This is the module I most need to trust. Every constant matches ScoringConfig
defaults; changes there will (correctly) break these tests.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from trend_radar.config import ScoringConfig
from trend_radar.models import NormalizedTopic, RawItem, TopicCluster
from trend_radar.scoring import (
    assign_source_percentiles,
    compute_velocity,
    score_clusters,
)

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


def _item(source: str, source_id: str, *, score: float, age_hours: float, title: str = "t") -> RawItem:
    return RawItem(
        source=source,  # type: ignore[arg-type]
        source_id=source_id,
        title=title,
        url=f"https://example.com/{source_id}",
        permalink=f"https://example.com/perma/{source_id}" if source != "arxiv" else None,
        raw_score=score,
        comment_count=None,
        created_at=NOW - timedelta(hours=age_hours),
    )


def _cluster(items: list[RawItem], title: str = "Test topic") -> TopicCluster:
    return TopicCluster(
        topic=NormalizedTopic(canonical_title=title, one_line=title, entities=[], tags=[]),
        items=items,
    )


# --- Velocity ------------------------------------------------------------

class TestVelocity:
    def test_matches_hand_computed(self) -> None:
        """velocity = raw_score / (age_hours + 2)^1.5"""
        cfg = ScoringConfig()  # defaults: offset=2.0, exp=1.5
        item = _item("hackernews", "a", score=100.0, age_hours=6.0)
        # 100 / (6+2)^1.5 = 100 / 8^1.5 = 100 / 22.627416997969522
        expected = 100.0 / (8.0 ** 1.5)
        assert compute_velocity(item, NOW, cfg) == pytest.approx(expected)

    def test_zero_score_zero_velocity(self) -> None:
        cfg = ScoringConfig()
        item = _item("arxiv", "z", score=0.0, age_hours=6.0)
        assert compute_velocity(item, NOW, cfg) == 0.0

    def test_future_created_at_clamped(self) -> None:
        """Clock skew — a future timestamp shouldn't produce negative age."""
        cfg = ScoringConfig()
        item = _item("hackernews", "f", score=100.0, age_hours=-3.0)  # 3h in the future
        # age clamped to 0 → velocity = 100 / 2^1.5
        assert compute_velocity(item, NOW, cfg) == pytest.approx(100.0 / (2.0 ** 1.5))


# --- Source percentiles --------------------------------------------------

class TestSourcePercentiles:
    def test_hackernews_ranked_by_velocity(self) -> None:
        cfg = ScoringConfig()
        items = [
            _item("hackernews", "a", score=10.0, age_hours=6.0),
            _item("hackernews", "b", score=100.0, age_hours=6.0),
            _item("hackernews", "c", score=50.0, age_hours=6.0),
        ]
        vels = {(i.source, i.source_id): compute_velocity(i, NOW, cfg) for i in items}
        pcts = assign_source_percentiles(items, vels, cfg)
        # 3 items → (0.5)/3, (1.5)/3, (2.5)/3 = 0.1667, 0.5, 0.8333
        assert pcts[("hackernews", "a")] == pytest.approx(0.5 / 3)
        assert pcts[("hackernews", "c")] == pytest.approx(1.5 / 3)
        assert pcts[("hackernews", "b")] == pytest.approx(2.5 / 3)

    def test_hackernews_ranked_independently_of_baseline_sources(self) -> None:
        """HN is percentile-ranked; arXiv/HF get the baseline regardless of raw_score."""
        cfg = ScoringConfig()
        items = [
            _item("hackernews", "h1", score=100.0, age_hours=6.0),
            _item("arxiv", "a1", score=0.0, age_hours=6.0),
        ]
        vels = {(i.source, i.source_id): compute_velocity(i, NOW, cfg) for i in items}
        pcts = assign_source_percentiles(items, vels, cfg)
        assert pcts[("hackernews", "h1")] == pytest.approx(0.5)  # (0+0.5)/1 for a single-item pool
        assert pcts[("arxiv", "a1")] == pytest.approx(0.5)       # fixed baseline

    def test_arxiv_gets_fixed_baseline(self) -> None:
        cfg = ScoringConfig(baseline_source_percentile=0.5)
        items = [
            _item("arxiv", "a1", score=0.0, age_hours=6.0),
            _item("arxiv", "a2", score=0.0, age_hours=3.0),
        ]
        vels = {(i.source, i.source_id): compute_velocity(i, NOW, cfg) for i in items}
        pcts = assign_source_percentiles(items, vels, cfg)
        assert pcts[("arxiv", "a1")] == 0.5
        assert pcts[("arxiv", "a2")] == 0.5

    def test_huggingface_ranked_by_upvotes(self) -> None:
        """HF upvotes ARE a comparable score signal — it's percentile-ranked, not baselined."""
        cfg = ScoringConfig()
        items = [
            _item("huggingface", "p1", score=10.0, age_hours=6.0),
            _item("huggingface", "p2", score=200.0, age_hours=6.0),
            _item("huggingface", "p3", score=100.0, age_hours=6.0),
        ]
        vels = {(i.source, i.source_id): compute_velocity(i, NOW, cfg) for i in items}
        pcts = assign_source_percentiles(items, vels, cfg)
        # 3 items → (0.5)/3, (1.5)/3, (2.5)/3 by ascending velocity
        assert pcts[("huggingface", "p1")] == pytest.approx(0.5 / 3)
        assert pcts[("huggingface", "p3")] == pytest.approx(1.5 / 3)
        assert pcts[("huggingface", "p2")] == pytest.approx(2.5 / 3)

    def test_huggingface_and_hackernews_ranked_independently(self) -> None:
        """Each ranked source has its own pool — the top HF item and the top HN item
        both score near 1.0 within their own source, regardless of raw magnitude."""
        cfg = ScoringConfig()
        items = [
            _item("hackernews", "h1", score=1000.0, age_hours=6.0),
            _item("hackernews", "h2", score=10.0, age_hours=6.0),
            _item("huggingface", "p1", score=50.0, age_hours=6.0),
            _item("huggingface", "p2", score=5.0, age_hours=6.0),
        ]
        vels = {(i.source, i.source_id): compute_velocity(i, NOW, cfg) for i in items}
        pcts = assign_source_percentiles(items, vels, cfg)
        # Top of each source's pool → (1+0.5)/2 = 0.75
        assert pcts[("hackernews", "h1")] == pytest.approx(0.75)
        assert pcts[("huggingface", "p1")] == pytest.approx(0.75)
        # Bottom of each pool → (0+0.5)/2 = 0.25
        assert pcts[("hackernews", "h2")] == pytest.approx(0.25)
        assert pcts[("huggingface", "p2")] == pytest.approx(0.25)

    def test_baseline_is_configurable(self) -> None:
        cfg = ScoringConfig(baseline_source_percentile=0.7)
        items = [_item("arxiv", "a1", score=0.0, age_hours=6.0)]
        vels = {(i.source, i.source_id): compute_velocity(i, NOW, cfg) for i in items}
        pcts = assign_source_percentiles(items, vels, cfg)
        assert pcts[("arxiv", "a1")] == 0.7


# --- Cluster scoring end-to-end ------------------------------------------

class TestScoreClusters:
    def test_single_source_no_corroboration(self) -> None:
        cfg = ScoringConfig()
        item = _item("hackernews", "a", score=100.0, age_hours=6.0)
        [ranked] = score_clusters([_cluster([item])], {}, NOW, cfg, max_items_per_topic=5)

        expected_velocity = 100.0 / (8.0 ** 1.5)
        assert ranked.score.velocity == pytest.approx(expected_velocity)
        assert ranked.score.source_percentile == pytest.approx(0.5)  # n=1
        assert ranked.score.corroboration_bonus == 0.0
        assert ranked.score.novelty_multiplier == 1.0
        assert ranked.score.final_score == pytest.approx(0.5)
        assert ranked.distinct_sources == 1
        assert ranked.suppressed is False

    def test_cross_source_corroboration_bonus(self) -> None:
        cfg = ScoringConfig()
        items = [
            _item("hackernews", "h1", score=50.0, age_hours=3.0),
            _item("arxiv", "a1", score=0.0, age_hours=6.0),
            _item("huggingface", "u1", score=100.0, age_hours=6.0),
        ]
        [ranked] = score_clusters([_cluster(items)], {}, NOW, cfg, max_items_per_topic=5)

        assert ranked.distinct_sources == 3
        # (3-1) * 0.15 = 0.30, right at the cap
        assert ranked.score.corroboration_bonus == pytest.approx(0.30)
        # All three items are top of their own pool → percentile 0.5 each → max 0.5
        assert ranked.score.source_percentile == pytest.approx(0.5)
        assert ranked.score.final_score == pytest.approx((0.5 + 0.30) * 1.0)

    def test_corroboration_capped_at_cap(self) -> None:
        """Prove the cap fires: with per_source > cap/(N-1) the raw sum would exceed it."""
        cfg = ScoringConfig(corroboration_per_source=0.20, corroboration_cap=0.30)
        # 3 sources × 0.20 per extra = raw (3-1)*0.20 = 0.40, but cap kicks in at 0.30
        items = [
            _item("hackernews", "h", score=10.0, age_hours=6.0),
            _item("arxiv", "a", score=0.0, age_hours=6.0),
            _item("huggingface", "u", score=1.0, age_hours=6.0),
        ]
        [ranked] = score_clusters([_cluster(items)], {}, NOW, cfg, max_items_per_topic=5)
        assert ranked.distinct_sources == 3
        assert ranked.score.corroboration_bonus == pytest.approx(0.30)  # capped, not 0.40

    def test_novelty_multiplier_applied(self) -> None:
        cfg = ScoringConfig()
        item = _item("hackernews", "r", score=100.0, age_hours=6.0)
        cluster = _cluster([item], title="Repeat topic")

        from trend_radar.models import compute_topic_id

        tid = compute_topic_id("Repeat topic")
        [ranked] = score_clusters(
            [cluster],
            {tid: (0.5, False, None)},  # half-novel
            NOW, cfg, max_items_per_topic=5,
        )
        assert ranked.score.novelty_multiplier == 0.5
        assert ranked.score.final_score == pytest.approx(0.5 * 0.5)  # (pct+corr) * novelty

    def test_suppressed_topic_flagged_with_reason(self) -> None:
        cfg = ScoringConfig()
        item = _item("hackernews", "r", score=100.0, age_hours=6.0)
        cluster = _cluster([item], title="Already covered")

        from trend_radar.models import compute_topic_id

        tid = compute_topic_id("Already covered")
        [ranked] = score_clusters(
            [cluster],
            {tid: (0.0, True, "similar to previously covered piece")},
            NOW, cfg, max_items_per_topic=5,
        )
        assert ranked.suppressed is True
        assert ranked.suppression_reason == "similar to previously covered piece"
        assert ranked.score.final_score == 0.0

    def test_sorted_by_final_score_desc(self) -> None:
        cfg = ScoringConfig()
        c1 = _cluster([_item("hackernews", "a", score=10.0, age_hours=6.0)], title="A")
        c2 = _cluster(
            [
                _item("hackernews", "b", score=100.0, age_hours=6.0),
                _item("arxiv", "b2", score=0.0, age_hours=6.0),
            ],
            title="B",
        )
        ranked = score_clusters([c1, c2], {}, NOW, cfg, max_items_per_topic=5)
        assert ranked[0].topic.canonical_title == "B"  # cross-source, gets corroboration
        assert ranked[0].score.final_score > ranked[1].score.final_score

    def test_top_n_items_kept_per_topic(self) -> None:
        cfg = ScoringConfig()
        items = [
            _item("hackernews", f"i{n}", score=float(n * 10), age_hours=6.0)
            for n in range(10)
        ]
        [ranked] = score_clusters([_cluster(items)], {}, NOW, cfg, max_items_per_topic=3)
        assert len(ranked.items) == 3
        # Highest-velocity items retained (i9, i8, i7)
        assert {si.item.source_id for si in ranked.items} == {"i9", "i8", "i7"}

    def test_scored_items_carry_per_item_numbers(self) -> None:
        cfg = ScoringConfig()
        items = [
            _item("hackernews", f"i{n}", score=float(n * 10), age_hours=6.0)
            for n in range(5)
        ]
        [ranked] = score_clusters([_cluster(items)], {}, NOW, cfg, max_items_per_topic=5)
        # Ordered by velocity DESC → first item is the highest
        assert ranked.items[0].velocity > ranked.items[-1].velocity
        # All percentiles in [0, 1]
        assert all(0.0 <= si.source_percentile <= 1.0 for si in ranked.items)

    def test_explanation_string_is_deterministic(self) -> None:
        cfg = ScoringConfig()
        item = _item("hackernews", "r", score=100.0, age_hours=6.0)
        [ranked] = score_clusters([_cluster([item])], {}, NOW, cfg, max_items_per_topic=5)
        # Format: "pct=X.XX + corr=X.XX (N source) = X.XX, novelty×X.XX → X.XX"
        assert "pct=0.50" in ranked.score.explanation
        assert "corr=0.00" in ranked.score.explanation
        assert "1 source" in ranked.score.explanation
        assert "novelty×1.00" in ranked.score.explanation


class TestEdgeCases:
    def test_empty_input(self) -> None:
        assert score_clusters([], {}, NOW, ScoringConfig(), max_items_per_topic=5) == []

    def test_empty_cluster_raises(self) -> None:
        cfg = ScoringConfig()
        empty = TopicCluster(
            topic=NormalizedTopic(canonical_title="x", one_line="x", entities=[], tags=[]),
            items=[],
        )
        with pytest.raises(ValueError, match="empty cluster"):
            score_clusters([empty], {}, NOW, cfg, max_items_per_topic=5)
