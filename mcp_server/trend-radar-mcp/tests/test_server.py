"""Server surface tests: the six tools exist, have valid schemas, correct annotations,
and wired tool bodies work when invoked with a fake Context.

These tests import the server module directly — no stdio subprocess. The
tool inventory tests exercise what an MCP client sees at list_tools; the
body tests exercise the actual work each tool does.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from trend_radar.config import AppSettings
from trend_radar.server import AppState, mcp
from trend_radar.storage import Storage

EXPECTED_TOOLS: set[str] = {
    "get_trending_topics",
    "explain_ranking",
    "check_novelty",
    "mark_covered",
    "list_covered",
    "get_source_config",
}


@pytest.fixture()
def tools() -> list:
    """The list of registered Tool objects."""
    return mcp._tool_manager.list_tools()  # noqa: SLF001 — inspecting for test coverage


class TestToolInventory:
    def test_exactly_six_tools(self, tools: list) -> None:
        assert len(tools) == 6, [t.name for t in tools]

    def test_expected_names(self, tools: list) -> None:
        assert {t.name for t in tools} == EXPECTED_TOOLS


class TestToolMetadata:
    @pytest.mark.parametrize("tool_name", sorted(EXPECTED_TOOLS))
    def test_has_nonempty_description(self, tool_name: str, tools: list) -> None:
        tool = next(t for t in tools if t.name == tool_name)
        assert tool.description, f"{tool_name} missing description"
        # Descriptions guide the LLM — a one-word description is a bug.
        assert len(tool.description) > 30, f"{tool_name} description too short"

    @pytest.mark.parametrize("tool_name", sorted(EXPECTED_TOOLS))
    def test_has_output_schema(self, tool_name: str, tools: list) -> None:
        tool = next(t for t in tools if t.name == tool_name)
        # Pydantic BaseModel returns → structured output → outputSchema populated.
        assert tool.output_schema is not None, f"{tool_name} has no outputSchema"
        assert tool.output_schema.get("type") == "object"

    @pytest.mark.parametrize("tool_name", sorted(EXPECTED_TOOLS))
    def test_has_input_schema(self, tool_name: str, tools: list) -> None:
        tool = next(t for t in tools if t.name == tool_name)
        assert tool.parameters is not None
        assert tool.parameters.get("type") == "object"


class TestAnnotations:
    """Behavior hints must match §6 of the spec."""

    _READ_ONLY = {
        "get_trending_topics", "explain_ranking", "check_novelty",
        "list_covered", "get_source_config",
    }
    _WRITE = {"mark_covered"}

    def test_read_only_tools_marked(self, tools: list) -> None:
        for t in tools:
            if t.name in self._READ_ONLY:
                assert t.annotations is not None, f"{t.name} missing annotations"
                assert t.annotations.read_only_hint is True, f"{t.name} should be readOnly"

    def test_mark_covered_is_idempotent(self, tools: list) -> None:
        t = next(t for t in tools if t.name == "mark_covered")
        assert t.annotations is not None
        assert t.annotations.read_only_hint is False
        assert t.annotations.idempotent_hint is True
        assert t.annotations.destructive_hint is False

    def test_get_trending_topics_open_world(self, tools: list) -> None:
        t = next(t for t in tools if t.name == "get_trending_topics")
        assert t.annotations is not None
        assert t.annotations.open_world_hint is True


class TestServerIdentity:
    def test_name_and_version(self) -> None:
        assert mcp.name == "trend-radar"
        # version accessor lives on the underlying low-level server
        assert mcp._lowlevel_server.version == "0.1.0"  # noqa: SLF001


# --------------------------------------------------------------------------
# Tool-body tests — fake Context, real work
# --------------------------------------------------------------------------

@dataclass
class _FakeRequestContext:
    lifespan_context: Any


@dataclass
class _FakeCtx:
    """Just enough Context-like surface for tools that only read lifespan_context."""
    request_context: Any


@pytest.fixture()
async def state(tmp_path: Path) -> AppState:
    """AppState with a temp SQLite DB and a real (unmocked) httpx client."""
    settings = AppSettings()
    storage = Storage(tmp_path / "trend.db")
    await storage.connect()
    client = httpx.AsyncClient()
    try:
        yield AppState(settings=settings, storage=storage, client=client)
    finally:
        await client.aclose()
        await storage.close()


@pytest.fixture()
def fake_ctx(state: AppState) -> _FakeCtx:
    return _FakeCtx(request_context=_FakeRequestContext(lifespan_context=state))


class TestGetSourceConfig:
    @pytest.mark.asyncio
    async def test_reports_hackernews_arxiv_hf_as_credentialed(self, fake_ctx: _FakeCtx) -> None:
        from trend_radar.server import get_source_config

        cfg = await get_source_config(fake_ctx)
        by_name = {s.name: s for s in cfg.sources}
        assert by_name["hackernews"].credentials_present is True
        assert by_name["arxiv"].credentials_present is True
        assert by_name["huggingface"].credentials_present is True

    @pytest.mark.asyncio
    async def test_exactly_three_sources_configured(self, fake_ctx: _FakeCtx) -> None:
        from trend_radar.server import get_source_config

        cfg = await get_source_config(fake_ctx)
        assert {s.name for s in cfg.sources} == {"hackernews", "arxiv", "huggingface"}


class TestCheckNovelty:
    @pytest.mark.asyncio
    async def test_novel_when_ledger_empty(self, fake_ctx: _FakeCtx) -> None:
        from trend_radar.server import check_novelty

        r = await check_novelty(fake_ctx, title="Anything at all")
        assert r.is_novel is True
        assert r.max_similarity == 0.0

    @pytest.mark.asyncio
    async def test_suppressed_after_marking_covered(
        self, fake_ctx: _FakeCtx, state: AppState
    ) -> None:
        from datetime import date

        from trend_radar.models import CoveredTopic
        from trend_radar.server import check_novelty

        await state.storage.upsert_covered(
            CoveredTopic(topic_id="a" * 12, canonical_title="Claude Opus 5 released",
                         covered_on=date(2026, 8, 20)),
            one_line="Anthropic ships Opus 5",
        )
        r = await check_novelty(
            fake_ctx, title="Claude Opus 5 released", one_line="Anthropic ships Opus 5"
        )
        assert r.is_novel is False
        assert r.closest_match_title == "Claude Opus 5 released"


class TestListCovered:
    @pytest.mark.asyncio
    async def test_empty_by_default(self, fake_ctx: _FakeCtx) -> None:
        from trend_radar.server import list_covered

        r = await list_covered(fake_ctx)
        assert r.days == 90
        assert r.items == []

    @pytest.mark.asyncio
    async def test_returns_marked_topics(self, fake_ctx: _FakeCtx, state: AppState) -> None:
        from datetime import date

        from trend_radar.models import CoveredTopic
        from trend_radar.server import list_covered

        await state.storage.upsert_covered(
            CoveredTopic(topic_id="b" * 12, canonical_title="Some topic",
                         covered_on=date(2026, 8, 20))
        )
        r = await list_covered(fake_ctx, days=30, limit=10)
        assert len(r.items) == 1
        assert r.items[0].canonical_title == "Some topic"


class TestMarkCovered:
    @pytest.mark.asyncio
    async def test_requires_topic_id_or_canonical_title(self, fake_ctx: _FakeCtx) -> None:
        from trend_radar.server import mark_covered

        with pytest.raises(ValueError, match=r"invalid_argument"):
            await mark_covered(fake_ctx)

    @pytest.mark.asyncio
    async def test_canonical_title_only_computes_topic_id(self, fake_ctx: _FakeCtx) -> None:
        from trend_radar.server import mark_covered

        r = await mark_covered(fake_ctx, canonical_title="A brand new topic")
        assert len(r.topic_id) == 12
        assert r.was_new is True
        assert r.canonical_title == "A brand new topic"

    @pytest.mark.asyncio
    async def test_idempotent_returns_was_new_false_second_time(
        self, fake_ctx: _FakeCtx
    ) -> None:
        from trend_radar.server import mark_covered

        r1 = await mark_covered(fake_ctx, canonical_title="Repeatable topic")
        assert r1.was_new is True
        r2 = await mark_covered(fake_ctx, canonical_title="Repeatable topic")
        assert r2.was_new is False
        assert r1.topic_id == r2.topic_id

    @pytest.mark.asyncio
    async def test_topic_id_without_run_cache_errors_helpfully(
        self, fake_ctx: _FakeCtx
    ) -> None:
        from trend_radar.server import mark_covered

        with pytest.raises(ValueError) as exc_info:
            await mark_covered(fake_ctx, topic_id="a" * 12)
        assert "not_found" in str(exc_info.value)
        assert "call get_trending_topics" in str(exc_info.value)


class TestExplainRanking:
    @pytest.mark.asyncio
    async def test_no_runs_yet_errors_with_next_step(self, fake_ctx: _FakeCtx) -> None:
        from trend_radar.server import explain_ranking

        with pytest.raises(ValueError) as exc_info:
            await explain_ranking(fake_ctx, topic_id="a" * 12)
        msg = str(exc_info.value)
        assert "no runs" in msg
        assert "get_trending_topics" in msg

    @pytest.mark.asyncio
    async def test_topic_not_in_last_run(
        self, fake_ctx: _FakeCtx, state: AppState
    ) -> None:
        from datetime import UTC, datetime

        from trend_radar.models import (
            NormalizedTopic, RankedTopic, RawItem, ScoreBreakdown, ScoredItem,
            SourceHealth, TrendingResult,
        )
        from trend_radar.server import explain_ranking

        # Seed a run cache with a single topic
        item = RawItem(
            source="hackernews", source_id="x1", title="t",
            url="https://example.com/x1", permalink="https://news.ycombinator.com/item?id=x1",
            raw_score=10.0, comment_count=1,
            created_at=datetime(2026, 8, 24, 6, 0, tzinfo=UTC),
        )
        run = TrendingResult(
            run_id="run-seed",
            generated_at=datetime(2026, 8, 24, 12, 0, tzinfo=UTC),
            lookback_hours=24, clustering_method="lexical",
            source_health=[SourceHealth(source="hackernews", ok=True)],
            topics=[RankedTopic(
                topic_id="known0000000",
                topic=NormalizedTopic(canonical_title="t", one_line="o", entities=[], tags=[]),
                items=[ScoredItem(item=item, velocity=1.0, source_percentile=0.5)],
                distinct_sources=1,
                score=ScoreBreakdown(
                    velocity=1.0, source_percentile=0.5, corroboration_bonus=0.0,
                    novelty_multiplier=1.0, final_score=0.5, explanation="x",
                ),
            )],
        )
        await state.storage.save_run(run)

        with pytest.raises(ValueError, match="not_found"):
            await explain_ranking(fake_ctx, topic_id="missing00000")

    @pytest.mark.asyncio
    async def test_returns_full_breakdown_for_known_topic(
        self, fake_ctx: _FakeCtx, state: AppState
    ) -> None:
        from datetime import UTC, datetime

        from trend_radar.models import (
            NormalizedTopic, RankedTopic, RawItem, ScoreBreakdown, ScoredItem,
            SourceHealth, TrendingResult,
        )
        from trend_radar.server import explain_ranking

        item = RawItem(
            source="hackernews", source_id="x1", title="t",
            url="https://example.com/x1", permalink="https://news.ycombinator.com/item?id=x1",
            raw_score=10.0, comment_count=1,
            created_at=datetime(2026, 8, 24, 6, 0, tzinfo=UTC),
        )
        run = TrendingResult(
            run_id="run-seed",
            generated_at=datetime(2026, 8, 24, 12, 0, tzinfo=UTC),
            lookback_hours=24, clustering_method="lexical",
            source_health=[SourceHealth(source="hackernews", ok=True)],
            topics=[RankedTopic(
                topic_id="known0000000",
                topic=NormalizedTopic(canonical_title="Test topic", one_line="o", entities=[], tags=[]),
                items=[ScoredItem(item=item, velocity=1.42, source_percentile=0.75)],
                distinct_sources=1,
                score=ScoreBreakdown(
                    velocity=1.42, source_percentile=0.75, corroboration_bonus=0.0,
                    novelty_multiplier=1.0, final_score=0.75, explanation="test explanation",
                ),
            )],
        )
        await state.storage.save_run(run)

        r = await explain_ranking(fake_ctx, topic_id="known0000000")
        assert r.topic_id == "known0000000"
        assert r.run_id == "run-seed"
        assert r.canonical_title == "Test topic"
        assert r.score.final_score == 0.75
        assert len(r.contributing_items) == 1
        assert r.contributing_items[0].velocity == 1.42
        assert r.contributing_items[0].source_percentile == 0.75
