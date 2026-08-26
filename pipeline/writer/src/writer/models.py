"""Data contract for the writer stage.

Input models mirror the subset of trend-radar's `TrendingResult` that
this stage consumes. Source of truth for input shapes is
`mcp_server/trend-radar-mcp/src/trend_radar/models.py`; we only
re-declare the fields the writer actually reads, and set
`extra='ignore'` so trend-radar can add fields without breaking us.

Output models (`DraftSource`, `Draft`, `DraftBundle`) are what the
renderer stage consumes.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

_lax = ConfigDict(extra="ignore")

SourceName = Literal["hackernews", "arxiv", "huggingface"]


# --- Input: subset of trend-radar's TrendingResult -----------------------

class RawItemIn(BaseModel):
    model_config = _lax
    source: SourceName
    title: str
    url: HttpUrl
    permalink: HttpUrl | None = None
    raw_score: float = 0.0
    comment_count: int | None = None
    body_excerpt: str | None = None


class ScoredItemIn(BaseModel):
    model_config = _lax
    item: RawItemIn
    velocity: float
    source_percentile: float = Field(..., ge=0.0, le=1.0)


class NormalizedTopicIn(BaseModel):
    model_config = _lax
    canonical_title: str
    one_line: str
    entities: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class ScoreBreakdownIn(BaseModel):
    model_config = _lax
    velocity: float
    source_percentile: float
    corroboration_bonus: float
    novelty_multiplier: float
    final_score: float
    explanation: str


class RankedTopicIn(BaseModel):
    model_config = _lax
    topic_id: str
    topic: NormalizedTopicIn
    items: list[ScoredItemIn]
    distinct_sources: int = Field(..., ge=1)
    score: ScoreBreakdownIn
    suppressed: bool = False


class TrendingResultIn(BaseModel):
    model_config = _lax
    run_id: str
    generated_at: datetime
    topics: list[RankedTopicIn]


# --- Output: what the renderer consumes ----------------------------------

class DraftSource(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    url: HttpUrl
    source: SourceName
    signal: str = Field(
        ..., min_length=1, max_length=80,
        description="Compact evidence line, e.g. '428 points on HN', 'top-decile arXiv velocity'.",
    )


class Draft(BaseModel):
    """One narrative draft, one topic. Emitted by the writer, consumed by the renderer."""

    topic_id: str = Field(
        ...,
        description="Echoed from RankedTopic.topic_id so downstream mark_covered can close the loop.",
    )
    canonical_title: str
    headline: str = Field(..., min_length=1, max_length=100)
    subhead: str = Field(..., min_length=1, max_length=200)
    body_md: str = Field(..., min_length=1, description="Markdown prose, ~150-300 words.")
    key_signals: list[str] = Field(..., min_length=1, max_length=5)
    sources: list[DraftSource] = Field(..., min_length=1)
    ranking_rationale: str = Field(
        ...,
        description="Verbatim ScoreBreakdown.explanation from trend-radar — never LLM-authored.",
    )


class DraftBundle(BaseModel):
    """Writer's top-level output: all drafts from one trending run."""

    run_id: str
    generated_at: datetime
    drafts: list[Draft]
