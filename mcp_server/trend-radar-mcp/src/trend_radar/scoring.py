"""Deterministic ranking. Pure Python, no LLM.

Every number in the final score derives arithmetically from item metadata
and the constants in ScoringConfig. explain_ranking exposes each ingredient.

Formulas (§3 of the spec):
- velocity = raw_score / (age_hours + offset)^exponent
- source_percentile: per-source rank / n for hn and huggingface (both have
  score signals: HN points, HF upvotes); fixed baseline for arxiv
- corroboration = min((distinct_sources - 1) * per_source, cap)
- final = (source_percentile + corroboration) * novelty_multiplier
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from trend_radar.config import ScoringConfig
from trend_radar.models import (
    RankedTopic,
    RawItem,
    ScoreBreakdown,
    ScoredItem,
    SourceName,
    TopicCluster,
    compute_topic_id,
)

# arXiv is the only baseline source — paper listings carry no comparable score
# signal. HN uses `points` and HF uses `upvotes`; both are percentile-ranked
# within their own source pool so cross-source values stay comparable.
_BASELINE_SOURCES: set[SourceName] = {"arxiv"}

# (source, source_id) tuple used as a stable per-item key across dicts.
ItemKey = tuple[SourceName, str]


def compute_velocity(item: RawItem, now: datetime, config: ScoringConfig) -> float:
    """HN-style gravity: raw_score / (age_hours + offset)^exponent.

    Age is clamped at 0 — a slightly-future created_at (clock skew between
    the API and us) shouldn't produce a negative divisor.
    """
    age_hours = max(0.0, (now - item.created_at).total_seconds() / 3600.0)
    denom = (age_hours + config.velocity_age_offset_hours) ** config.velocity_exponent
    return item.raw_score / denom


def assign_source_percentiles(
    items: Iterable[RawItem],
    velocities: dict[ItemKey, float],
    config: ScoringConfig,
) -> dict[ItemKey, float]:
    """Per-item percentile within its own source for this run.

    - HN and HF: sort ascending by velocity, percentile = (rank + 0.5)/n.
      Top item → close to 1.0; bottom → close to 0.
    - arXiv: fixed baseline (§3 — paper listings carry no score signal).
    """
    percentiles: dict[ItemKey, float] = {}
    by_source: dict[SourceName, list[RawItem]] = {}
    for item in items:
        by_source.setdefault(item.source, []).append(item)

    for source, pool in by_source.items():
        if source in _BASELINE_SOURCES:
            for item in pool:
                percentiles[(source, item.source_id)] = config.baseline_source_percentile
            continue
        n = len(pool)
        ranked = sorted(pool, key=lambda i: velocities[(i.source, i.source_id)])
        for rank, item in enumerate(ranked):
            percentiles[(item.source, item.source_id)] = (rank + 0.5) / n
    return percentiles


def score_clusters(
    clusters: list[TopicCluster],
    novelty_by_topic_id: dict[str, tuple[float, bool, str | None]],
    now: datetime,
    config: ScoringConfig,
    max_items_per_topic: int,
) -> list[RankedTopic]:
    """Score every cluster. Returns topics sorted by final_score DESC.

    `novelty_by_topic_id` maps topic_id → (multiplier, suppressed, reason).
    Missing keys default to (1.0, False, None) — novel by construction.
    """
    all_items = [item for c in clusters for item in c.items]
    velocities: dict[ItemKey, float] = {
        (i.source, i.source_id): compute_velocity(i, now, config) for i in all_items
    }
    percentiles = assign_source_percentiles(all_items, velocities, config)

    ranked = [
        _score_one(c, velocities, percentiles, novelty_by_topic_id, config, max_items_per_topic)
        for c in clusters
    ]
    ranked.sort(key=lambda r: r.score.final_score, reverse=True)
    return ranked


def _score_one(
    cluster: TopicCluster,
    velocities: dict[ItemKey, float],
    percentiles: dict[ItemKey, float],
    novelty_by_topic_id: dict[str, tuple[float, bool, str | None]],
    config: ScoringConfig,
    max_items_per_topic: int,
) -> RankedTopic:
    if not cluster.items:
        # Empty clusters aren't produced by our pipeline, but be explicit.
        raise ValueError("cannot score empty cluster")

    keys = [(i.source, i.source_id) for i in cluster.items]
    velocity = max(velocities[k] for k in keys)
    percentile = max(percentiles[k] for k in keys)

    distinct = len({i.source for i in cluster.items})
    corroboration = min(
        max(0, distinct - 1) * config.corroboration_per_source,
        config.corroboration_cap,
    )

    topic_id = compute_topic_id(cluster.topic.canonical_title)
    novelty_mult, suppressed, reason = novelty_by_topic_id.get(topic_id, (1.0, False, None))
    final = (percentile + corroboration) * novelty_mult

    explanation = (
        f"pct={percentile:.2f} + corr={corroboration:.2f} "
        f"({distinct} source{'s' if distinct != 1 else ''}) = {percentile + corroboration:.2f}, "
        f"novelty×{novelty_mult:.2f} → {final:.2f}"
    )

    # Cache only the top-N items by velocity per topic (per user's phase-1 call).
    top_items = sorted(
        cluster.items, key=lambda i: velocities[(i.source, i.source_id)], reverse=True
    )[:max_items_per_topic]
    scored_items = [
        ScoredItem(
            item=i,
            velocity=velocities[(i.source, i.source_id)],
            source_percentile=percentiles[(i.source, i.source_id)],
        )
        for i in top_items
    ]

    return RankedTopic(
        topic_id=topic_id,
        topic=cluster.topic,
        items=scored_items,
        distinct_sources=distinct,
        score=ScoreBreakdown(
            velocity=velocity,
            source_percentile=percentile,
            corroboration_bonus=corroboration,
            novelty_multiplier=novelty_mult,
            final_score=final,
            explanation=explanation,
        ),
        suppressed=suppressed,
        suppression_reason=reason,
    )
