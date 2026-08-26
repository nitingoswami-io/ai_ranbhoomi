"""MCP server: six tools over stdio, wired end-to-end.

Every tool body is thin — the work happens in `pipeline`, `storage`, and
`novelty`. Server-side concerns kept here:
- Lifespan: open storage + shared HTTP client once per process, tear down cleanly
- Error boundary: `@tool_boundary` turns any unexpected exception into a
  structured `[code] message / Next step: ...` error
- Type hints and annotations: what MCP clients see in tools/list
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, AsyncIterator

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field, HttpUrl

from trend_radar.config import AppSettings, get_settings
from trend_radar.errors import format_tool_error, tool_boundary
from trend_radar.http import create_http_client, now_utc
from trend_radar.models import (
    CoveredList,
    CoveredTopic,
    MarkCoveredResult,
    NormalizedTopic,
    NoveltyResult,
    RankingExplanation,
    ServerConfigView,
    SourceConfigInfo,
    SourceName,
    TrendingResult,
    compute_topic_id,
)
from trend_radar.novelty import NoveltyGate
from trend_radar.obs import get_logger, setup_logging
from trend_radar.pipeline import run_trending_pipeline
from trend_radar.storage import Storage

_ALL_SOURCES: tuple[SourceName, ...] = ("hackernews", "arxiv", "huggingface")


# --------------------------------------------------------------------------
# Lifespan — one storage connection + one HTTP client per process
# --------------------------------------------------------------------------

@dataclass
class AppState:
    settings: AppSettings
    storage: Storage
    client: httpx.AsyncClient


@asynccontextmanager
async def app_lifespan(_app: MCPServer) -> AsyncIterator[AppState]:
    settings = get_settings()
    setup_logging(settings)
    log = get_logger("server")

    storage = Storage(settings.db_path)
    await storage.connect()

    client = create_http_client()
    log.info(
        "trend-radar ready (db=%s, sources=%d, llm=%s)",
        settings.db_path,
        len(_ALL_SOURCES),
        "on" if settings.has_anthropic_key() else "off (lexical fallback)",
    )
    try:
        yield AppState(settings=settings, storage=storage, client=client)
    finally:
        log.info("trend-radar shutting down")
        await client.aclose()
        await storage.close()


def _state(ctx: Context) -> AppState:
    """Type-narrowed accessor. Raises ValueError if called outside a request
    (e.g. accidentally in test setup without a fake ctx)."""
    state = ctx.request_context.lifespan_context
    if not isinstance(state, AppState):
        raise ValueError(format_tool_error(
            "internal",
            "lifespan context is not initialized",
            "this is a server bug — file an issue with the traceback",
        ))
    return state


mcp: MCPServer[AppState] = MCPServer(
    name="trend-radar",
    title="Trend Radar",
    version="0.1.0",
    description=(
        "Surfaces trending AI/ML topics from Hacker News, arXiv, and Hugging Face "
        "Daily Papers. Deterministic scoring, LLM-clustered topics, novelty "
        "ledger to avoid repeating covered stories."
    ),
    instructions=(
        "Use `get_trending_topics` for the daily briefing. Call `explain_ranking` "
        "with a topic_id from the last run to see why it ranked where it did. "
        "Before writing up a topic, call `check_novelty`; after publishing, call "
        "`mark_covered` so it's excluded from future runs."
    ),
    lifespan=app_lifespan,
)


# --------------------------------------------------------------------------
# 1. get_trending_topics
# --------------------------------------------------------------------------

@mcp.tool(
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True),
    description=(
        "Run the full ingest→cluster→score→novelty-gate pipeline and return ranked "
        "trending AI/ML topics for the given lookback window. This is the primary "
        "tool — call it once at the start of a briefing session, then use "
        "`explain_ranking` and `check_novelty` against the returned topic_ids. "
        "Sources fetch in parallel; a dead source degrades the result but never "
        "fails the call — inspect `source_health` to see which succeeded."
    ),
)
@tool_boundary
async def get_trending_topics(
    ctx: Context,
    lookback_hours: Annotated[
        int, Field(gt=0, le=168, description="Look back this many hours. Default 24."),
    ] = 24,
    limit: Annotated[
        int, Field(gt=0, le=100, description="Max topics to return. Default 15."),
    ] = 15,
    include_suppressed: Annotated[
        bool, Field(description="Include novelty-suppressed topics (with suppressed=true)."),
    ] = True,
    sources: Annotated[
        list[SourceName] | None,
        Field(description="Restrict to a subset of sources. None = all four."),
    ] = None,
) -> TrendingResult:
    st = _state(ctx)
    return await run_trending_pipeline(
        st.settings,
        st.storage,
        st.client,
        lookback_hours=lookback_hours,
        limit=limit,
        include_suppressed=include_suppressed,
        sources=sources,
    )


# --------------------------------------------------------------------------
# 2. explain_ranking
# --------------------------------------------------------------------------

@mcp.tool(
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    description=(
        "Return the full arithmetic behind a topic's score: velocity, source "
        "percentile, corroboration bonus, novelty multiplier, and every contributing "
        "raw item with its own numbers. Answers 'why did this rank here?'. Reads from "
        "the cached last run — does not re-ingest. Pass a topic_id from a recent "
        "`get_trending_topics` result."
    ),
)
@tool_boundary
async def explain_ranking(
    ctx: Context,
    topic_id: Annotated[
        str, Field(min_length=12, max_length=12, description="12-char topic_id from a recent run."),
    ],
) -> RankingExplanation:
    st = _state(ctx)
    run = await st.storage.get_latest_run()
    if run is None:
        raise ValueError(format_tool_error(
            "not_found",
            "no runs are cached yet",
            "call get_trending_topics first, then use a topic_id from that response",
        ))
    topic = next((t for t in run.topics if t.topic_id == topic_id), None)
    if topic is None:
        raise ValueError(format_tool_error(
            "not_found",
            f"topic_id {topic_id!r} is not in the last run (run_id={run.run_id})",
            "call get_trending_topics to refresh, then use a topic_id from that response",
        ))
    return RankingExplanation(
        topic_id=topic.topic_id,
        run_id=run.run_id,
        canonical_title=topic.topic.canonical_title,
        score=topic.score,
        contributing_items=topic.items,
    )


# --------------------------------------------------------------------------
# 3. check_novelty
# --------------------------------------------------------------------------

@mcp.tool(
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    description=(
        "Check an arbitrary topic string against the coverage ledger without "
        "committing anything. Use before writing up a topic to confirm it's not a "
        "rehash. Returns similarity score and the closest match, if any, within "
        "the configured lookback window (default 90 days)."
    ),
)
@tool_boundary
async def check_novelty(
    ctx: Context,
    title: Annotated[
        str, Field(min_length=1, max_length=200, description="Proposed topic title to check."),
    ],
    one_line: Annotated[
        str | None,
        Field(default=None, max_length=300, description="Optional one-line summary; sharpens matching."),
    ] = None,
) -> NoveltyResult:
    st = _state(ctx)
    ledger = await st.storage.recent_covered_records(st.settings.scoring.novelty_lookback_days)
    gate = NoveltyGate(st.settings.scoring, embed_model=st.settings.embed_model)
    topic = NormalizedTopic(
        canonical_title=title[:80],
        one_line=(one_line or title)[:160],
        entities=[],
        tags=[],
    )
    return gate.check(topic, ledger)


# --------------------------------------------------------------------------
# 4. mark_covered
# --------------------------------------------------------------------------

@mcp.tool(
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    ),
    description=(
        "Record that a topic has been covered so future runs suppress it. Idempotent "
        "on topic_id — calling twice with the same topic_id updates the row rather "
        "than duplicating. Provide either `topic_id` (from a recent run) OR "
        "`canonical_title` (for ad-hoc coverage that didn't come from trend-radar)."
    ),
)
@tool_boundary
async def mark_covered(
    ctx: Context,
    topic_id: Annotated[
        str | None,
        Field(default=None, min_length=12, max_length=12, description="12-char id from a recent run."),
    ] = None,
    canonical_title: Annotated[
        str | None,
        Field(default=None, min_length=1, max_length=80, description="Free-text title; used if no topic_id."),
    ] = None,
    post_url: Annotated[
        HttpUrl | None,
        Field(default=None, description="URL of the published piece, for the ledger record."),
    ] = None,
    notes: Annotated[
        str | None,
        Field(default=None, max_length=500, description="Optional note (angle taken, related topics, etc)."),
    ] = None,
) -> MarkCoveredResult:
    if not topic_id and not canonical_title:
        raise ValueError(format_tool_error(
            "invalid_argument",
            "must provide either topic_id or canonical_title",
            "pass a topic_id from get_trending_topics, or provide a canonical_title string",
        ))

    st = _state(ctx)

    resolved_title = canonical_title
    resolved_one_line: str | None = None

    if topic_id and not canonical_title:
        run = await st.storage.get_latest_run()
        cached_topic = None
        if run is not None:
            cached_topic = next((t for t in run.topics if t.topic_id == topic_id), None)
        if cached_topic is None:
            raise ValueError(format_tool_error(
                "not_found",
                f"topic_id {topic_id!r} not found in the last run",
                "call get_trending_topics first, or pass canonical_title along with topic_id",
            ))
        resolved_title = cached_topic.topic.canonical_title
        resolved_one_line = cached_topic.topic.one_line

    assert resolved_title is not None  # guarded by the branches above
    tid = topic_id or compute_topic_id(resolved_title)

    entry = CoveredTopic(
        topic_id=tid,
        canonical_title=resolved_title,
        covered_on=now_utc().date(),
        post_url=post_url,
        notes=notes,
    )
    was_new = await st.storage.upsert_covered(entry, one_line=resolved_one_line)
    return MarkCoveredResult(
        topic_id=tid, canonical_title=resolved_title, was_new=was_new,
    )


# --------------------------------------------------------------------------
# 5. list_covered
# --------------------------------------------------------------------------

@mcp.tool(
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    description=(
        "List topics recorded in the coverage ledger, newest first. Useful for "
        "reviewing recent output or sanity-checking why a topic is being suppressed."
    ),
)
@tool_boundary
async def list_covered(
    ctx: Context,
    days: Annotated[int, Field(gt=0, le=365, description="Lookback in days. Default 90.")] = 90,
    limit: Annotated[int, Field(gt=0, le=500, description="Max rows to return. Default 100.")] = 100,
) -> CoveredList:
    st = _state(ctx)
    items = await st.storage.list_covered(days=days, limit=limit)
    return CoveredList(days=days, items=items)


# --------------------------------------------------------------------------
# 6. get_source_config
# --------------------------------------------------------------------------

@mcp.tool(
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    description=(
        "Diagnostic view of the server's runtime configuration: which sources are "
        "enabled, which have credentials present, and the "
        "scoring constants in effect. Call this first when a run misbehaves — "
        "misconfiguration is the most common cause. Never returns credential values."
    ),
)
@tool_boundary
async def get_source_config(ctx: Context) -> ServerConfigView:
    settings = _state(ctx).settings
    sources = [
        SourceConfigInfo(name="hackernews", enabled=True, credentials_present=True, notes="no auth needed"),
        SourceConfigInfo(name="arxiv", enabled=True, credentials_present=True, notes="no auth needed"),
        SourceConfigInfo(name="huggingface", enabled=True, credentials_present=True, notes="no auth needed"),
    ]
    return ServerConfigView(
        sources=sources,
        scoring=settings.scoring.model_dump(),
        db_path=str(settings.db_path),
        llm_model=settings.llm_model if settings.has_anthropic_key() else None,
        embed_model=settings.embed_model,
        max_items_per_topic=settings.max_items_per_topic,
    )


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main() -> None:
    # Lifespan does the heavy lifting; here we just start stdio.
    mcp.run(transport="stdio")
