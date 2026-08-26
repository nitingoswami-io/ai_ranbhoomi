"""Public data contract for trend-radar.

Every field name here is API surface — the downstream Pydantic AI writer
agent depends on these shapes. Do not rename without bumping a version.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

SourceName = Literal["hackernews", "arxiv", "huggingface"]
ClusteringMethod = Literal["llm", "lexical"]


# --- Ingest / normalize -----------------------------------------------------

class RawItem(BaseModel):
    """A single item pulled from one source, pre-clustering."""
    model_config = ConfigDict(frozen=True)

    source: SourceName
    source_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    url: HttpUrl
    permalink: HttpUrl | None = Field(
        None,
        description=(
            "Discussion thread URL, distinct from the linked artifact. "
            "None for arXiv/HF where the paper is the artifact."
        ),
    )
    raw_score: float = Field(..., ge=0)
    comment_count: int | None = Field(None, ge=0)
    created_at: datetime = Field(..., description="Timezone-aware UTC.")
    body_excerpt: str | None = Field(None, max_length=500)


class NormalizedTopic(BaseModel):
    """LLM (or lexical-fallback) output: what this topic actually is."""
    canonical_title: str = Field(..., min_length=1, max_length=80)
    one_line: str = Field(..., min_length=1, max_length=160)
    entities: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list, max_length=5)


class TopicCluster(BaseModel):
    """Clustering output, pre-scoring: canonical topic + the raw items in it."""
    topic: NormalizedTopic
    items: list[RawItem]


# --- Scoring ----------------------------------------------------------------

class ScoreBreakdown(BaseModel):
    """Every ingredient of the final score, so ranking is fully explainable."""
    velocity: float
    source_percentile: float = Field(..., ge=0.0, le=1.0)
    corroboration_bonus: float = Field(..., ge=0.0, le=0.30)
    novelty_multiplier: float = Field(..., ge=0.0, le=1.0)
    final_score: float
    explanation: str = Field(
        ...,
        description=(
            "One sentence, deterministically formatted from the numbers above. "
            "Never LLM-generated."
        ),
    )


class ScoredItem(BaseModel):
    """A RawItem paired with the per-item numbers that fed its cluster's score.

    Emitted by the scorer for the top-N items per topic. Enables explain_ranking
    to answer 'why did this specific item pull the score up?' without re-running
    the pipeline.
    """
    item: RawItem
    velocity: float = Field(..., description="raw_score / (age_h + offset)^exponent")
    source_percentile: float = Field(
        ..., ge=0.0, le=1.0,
        description="Percentile of this item's velocity within its source for this run.",
    )


class RankedTopic(BaseModel):
    topic_id: str = Field(..., description="Stable 12-char sha1 of the normalized canonical_title.")
    topic: NormalizedTopic
    items: list[ScoredItem] = Field(
        ...,
        description="Top-N exemplar items with per-item score components (N per TREND_RADAR_MAX_ITEMS_PER_TOPIC).",
    )
    distinct_sources: int = Field(..., ge=1, le=3)
    score: ScoreBreakdown
    suppressed: bool = False
    suppression_reason: str | None = None


# --- Novelty ledger ---------------------------------------------------------

class NoveltyResult(BaseModel):
    is_novel: bool
    max_similarity: float = Field(..., ge=0.0, le=1.0)
    closest_match_title: str | None = None
    closest_match_date: date | None = None


class CoveredTopic(BaseModel):
    topic_id: str
    canonical_title: str
    covered_on: date
    post_url: HttpUrl | None = None
    notes: str | None = None


# --- Tool return envelopes --------------------------------------------------

class SourceHealth(BaseModel):
    """Per-adapter status for one run — surfaced so partial failures are visible."""
    source: SourceName
    ok: bool
    items_fetched: int = 0
    error: str | None = None
    latency_ms: int | None = None


class TrendingResult(BaseModel):
    """Return type for get_trending_topics — the main tool."""
    run_id: str = Field(..., description="UUID for this run; use with explain_ranking.")
    generated_at: datetime
    lookback_hours: int
    clustering_method: ClusteringMethod
    source_health: list[SourceHealth]
    topics: list[RankedTopic]


class RankingExplanation(BaseModel):
    """Return type for explain_ranking. Every number that produced the final score."""
    topic_id: str
    run_id: str
    canonical_title: str
    score: ScoreBreakdown
    contributing_items: list[ScoredItem] = Field(
        ...,
        description="Per-item breakdowns: each item's velocity and within-source percentile.",
    )


class SourceConfigInfo(BaseModel):
    name: SourceName
    enabled: bool
    credentials_present: bool
    notes: str | None = None


class ServerConfigView(BaseModel):
    """Return type for get_source_config — diagnostic surface for misconfig debugging."""
    sources: list[SourceConfigInfo]
    scoring: dict[str, float]
    db_path: str
    llm_model: str | None
    embed_model: str | None
    max_items_per_topic: int


class MarkCoveredResult(BaseModel):
    topic_id: str
    canonical_title: str
    was_new: bool = Field(..., description="False if an idempotent write hit an existing row.")


class CoveredList(BaseModel):
    days: int
    items: list[CoveredTopic]


# --- Helpers ----------------------------------------------------------------

def compute_topic_id(canonical_title: str) -> str:
    """Stable 12-char sha1 of the normalized canonical_title.

    Normalization: lowercase, strip surrounding whitespace, collapse internal
    whitespace to single spaces. Punctuation retained — small differences
    ("GPT-5" vs "GPT 5") intentionally produce different ids; the clustering
    step is what unifies them upstream.
    """
    normalized = " ".join(canonical_title.lower().split())
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]
