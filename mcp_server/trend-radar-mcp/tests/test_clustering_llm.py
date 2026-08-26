"""LLM clustering tests with pydantic_ai FunctionModel.

Uses FunctionModel to inject deterministic tool-call responses so we can
verify the output validator's invariants (missing/invented/duplicate ids)
and the graceful-fallback path.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import SecretStr
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from trend_radar.clustering import _build_agent, _format_prompt, _materialize, cluster_items
from trend_radar.config import AppSettings
from trend_radar.models import NormalizedTopic, RawItem, TopicCluster

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


def _item(source: str, source_id: str, title: str) -> RawItem:
    return RawItem(
        source=source,  # type: ignore[arg-type]
        source_id=source_id,
        title=title,
        url=f"https://example.com/{source_id}",
        permalink=f"https://example.com/perma/{source_id}" if source != "arxiv" else None,
        raw_score=10.0,
        comment_count=None,
        created_at=NOW,
    )


def _one_shot_model(clusters: list[dict[str, Any]]) -> FunctionModel:
    """FunctionModel that returns `clusters` verbatim as the final result."""
    def fn(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tool_name = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(tool_name=tool_name, args={"response": clusters})])
    return FunctionModel(fn)


def _sequential_model(*responses: list[dict[str, Any]]) -> FunctionModel:
    """FunctionModel that yields a different response per call — for retry testing."""
    it = iter(responses)

    def fn(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tool_name = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(tool_name=tool_name, args={"response": next(it)})])

    return FunctionModel(fn)


class TestLLMHappyPath:
    @pytest.mark.asyncio
    async def test_valid_clusters_are_materialized(self) -> None:
        items = [
            _item("hackernews", "r1", "Claude Opus 5 released"),
            _item("hackernews", "h1", "Claude Opus 5 benchmarks"),
            _item("arxiv", "a1", "New attention mechanism"),
        ]
        response = [
            {
                "canonical_title": "Claude Opus 5 launch",
                "one_line": "Anthropic ships Opus 5, benchmark posts spike",
                "entities": ["Anthropic", "Claude Opus 5"],
                "tags": ["release", "anthropic"],
                "item_ids": ["hackernews:r1", "hackernews:h1"],
            },
            {
                "canonical_title": "New attention mechanism paper",
                "one_line": "arXiv paper proposes state-space attention variant",
                "entities": [],
                "tags": ["research"],
                "item_ids": ["arxiv:a1"],
            },
        ]
        agent = _build_agent("test")
        deps = {f"{i.source}:{i.source_id}" for i in items}

        r = await agent.run(_format_prompt(items), deps=deps, model=_one_shot_model(response))
        clusters = _materialize(r.output, items)

        assert len(clusters) == 2
        big = next(c for c in clusters if "Claude" in c.topic.canonical_title)
        assert {i.source_id for i in big.items} == {"r1", "h1"}
        assert big.topic.entities == ["Anthropic", "Claude Opus 5"]


class TestOutputValidator:
    @pytest.mark.asyncio
    async def test_missing_ids_trigger_retry_then_recover(self) -> None:
        items = [_item("hackernews", "r1", "A"), _item("hackernews", "h1", "B")]
        bad = [{"canonical_title": "X", "one_line": "X", "entities": [], "tags": [],
                "item_ids": ["hackernews:h1"]}]
        good = [{"canonical_title": "X", "one_line": "X", "entities": [], "tags": [],
                 "item_ids": ["hackernews:r1", "hackernews:h1"]}]
        agent = _build_agent("test")
        deps = {"hackernews:r1", "hackernews:h1"}

        r = await agent.run(_format_prompt(items), deps=deps, model=_sequential_model(bad, good))
        assert len(r.output) == 1
        assert set(r.output[0].item_ids) == deps

    @pytest.mark.asyncio
    async def test_invented_ids_trigger_retry(self) -> None:
        items = [_item("hackernews", "r1", "A")]
        bad = [{"canonical_title": "X", "one_line": "X", "entities": [], "tags": [],
                "item_ids": ["hackernews:r1", "hackernews:ghost"]}]
        good = [{"canonical_title": "X", "one_line": "X", "entities": [], "tags": [],
                 "item_ids": ["hackernews:r1"]}]
        agent = _build_agent("test")
        r = await agent.run(_format_prompt(items), deps={"hackernews:r1"}, model=_sequential_model(bad, good))
        assert r.output[0].item_ids == ["hackernews:r1"]

    @pytest.mark.asyncio
    async def test_duplicate_ids_trigger_retry(self) -> None:
        items = [_item("hackernews", "r1", "A"), _item("hackernews", "h1", "B")]
        bad = [
            {"canonical_title": "X", "one_line": "X", "entities": [], "tags": [],
             "item_ids": ["hackernews:r1", "hackernews:h1"]},
            {"canonical_title": "Y", "one_line": "Y", "entities": [], "tags": [],
             "item_ids": ["hackernews:r1"]},
        ]
        good = [
            {"canonical_title": "X", "one_line": "X", "entities": [], "tags": [],
             "item_ids": ["hackernews:r1"]},
            {"canonical_title": "Y", "one_line": "Y", "entities": [], "tags": [],
             "item_ids": ["hackernews:h1"]},
        ]
        agent = _build_agent("test")
        r = await agent.run(
            _format_prompt(items),
            deps={"hackernews:r1", "hackernews:h1"},
            model=_sequential_model(bad, good),
        )
        all_ids = sorted(iid for c in r.output for iid in c.item_ids)
        assert all_ids == ["hackernews:h1", "hackernews:r1"]


class TestFallback:
    @pytest.mark.asyncio
    async def test_falls_back_when_llm_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = AppSettings().model_copy(update={"anthropic_api_key": SecretStr("test-key")})
        assert settings.has_anthropic_key()

        import trend_radar.clustering as mod

        async def _boom(items, settings):  # noqa: ARG001
            raise RuntimeError("simulated LLM outage")

        monkeypatch.setattr(mod, "_llm_cluster", _boom)

        items = [_item("hackernews", "r1", "test")]
        clusters, method = await cluster_items(items, settings)
        assert method == "lexical"
        assert len(clusters) == 1

    @pytest.mark.asyncio
    async def test_reports_llm_method_on_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = AppSettings().model_copy(update={"anthropic_api_key": SecretStr("test-key")})

        import trend_radar.clustering as mod

        async def _fake_llm(items, settings):  # noqa: ARG001
            return [
                TopicCluster(
                    topic=NormalizedTopic(canonical_title="X", one_line="X", entities=[], tags=[]),
                    items=list(items),
                )
            ]

        monkeypatch.setattr(mod, "_llm_cluster", _fake_llm)

        _, method = await cluster_items([_item("hackernews", "r1", "t")], settings)
        assert method == "llm"
