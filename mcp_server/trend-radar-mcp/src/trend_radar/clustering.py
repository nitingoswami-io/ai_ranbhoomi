"""Topic clustering: Pydantic AI normalizer with a rapidfuzz fallback.

Per §4 of the spec, this is the only place Pydantic AI is used. One agent
call per pipeline run — items are batched into a single prompt. If the LLM
call fails after two retries, or if ANTHROPIC_API_KEY is absent, we
transparently fall back to greedy pairwise rapidfuzz clustering and report
`method="lexical"` in the tool response.
"""
from __future__ import annotations

from collections import Counter

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from rapidfuzz import fuzz

from trend_radar.config import AppSettings
from trend_radar.models import (
    ClusteringMethod,
    NormalizedTopic,
    RawItem,
    TopicCluster,
)
from trend_radar.obs import get_logger

_LOG = get_logger("clustering")
_LEXICAL_THRESHOLD = 82  # per spec §4


class _ClusterOutput(BaseModel):
    """LLM-facing cluster shape — items referenced by composite id."""

    canonical_title: str = Field(..., min_length=1, max_length=80)
    one_line: str = Field(..., min_length=1, max_length=160)
    entities: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list, max_length=5)
    item_ids: list[str] = Field(..., min_length=1)


_SYSTEM_PROMPT = """You cluster AI/ML news items by topic.

Input: a list of items, each on its own line, in the form:
    <source>:<source_id>: <title>

Output: a JSON array of clusters. Each cluster has:
- canonical_title: the crispest one-line phrasing of the topic (<=80 chars)
- one_line: a plain-language sentence describing the topic (<=160 chars)
- entities: named models, papers, companies, or libraries mentioned
- tags: 0-5 short lowercase tags
- item_ids: the composite ids of items belonging to this cluster

Hard rules:
1. Every input id must appear in exactly one cluster. No id skipped, no id invented.
2. Group items about the same story/release/paper even when titles differ.
3. Do NOT split items on source alone. An HN discussion, an arXiv paper, and a
   Hugging Face daily-papers entry about the same result belong together.
4. Do NOT merge distinct topics that share a keyword.
"""


async def cluster_items(
    items: list[RawItem], settings: AppSettings
) -> tuple[list[TopicCluster], ClusteringMethod]:
    """Return (clusters, method). Empty input yields an empty list."""
    if not items:
        return [], "lexical"

    if settings.has_anthropic_key():
        try:
            return await _llm_cluster(items, settings), "llm"
        except Exception as exc:  # noqa: BLE001 — degradation must be total
            _LOG.warning("LLM clustering failed, falling back to lexical: %s", exc)

    return _lexical_cluster(items), "lexical"


# --- LLM path --------------------------------------------------------------

async def _llm_cluster(items: list[RawItem], settings: AppSettings) -> list[TopicCluster]:
    all_ids = {_item_key(i) for i in items}
    agent = _build_agent(settings.llm_model)
    result = await agent.run(_format_prompt(items), deps=all_ids)
    return _materialize(result.output, items)


def _build_agent(model: str) -> Agent[set[str], list[_ClusterOutput]]:
    agent: Agent[set[str], list[_ClusterOutput]] = Agent(
        model=model,
        output_type=list[_ClusterOutput],
        deps_type=set[str],
        system_prompt=_SYSTEM_PROMPT,
        retries=2,
    )

    @agent.output_validator
    def _validate(
        ctx: RunContext[set[str]], output: list[_ClusterOutput]
    ) -> list[_ClusterOutput]:
        if not output:
            raise ModelRetry("Empty cluster list — return at least one cluster.")
        returned = [iid for c in output for iid in c.item_ids]
        returned_set = set(returned)
        missing = ctx.deps - returned_set
        if missing:
            sample = sorted(missing)[:5]
            raise ModelRetry(
                f"{len(missing)} input ids not assigned to any cluster: {sample}. "
                "Every input id must appear exactly once."
            )
        invented = returned_set - ctx.deps
        if invented:
            sample = sorted(invented)[:5]
            raise ModelRetry(
                f"{len(invented)} ids appear in your output that were not in the input: {sample}. "
                "Return only ids that were provided."
            )
        if len(returned) != len(returned_set):
            dups = [k for k, v in Counter(returned).items() if v > 1][:5]
            raise ModelRetry(
                f"These ids appear in multiple clusters: {dups}. Each id must appear once."
            )
        return output

    return agent


def _format_prompt(items: list[RawItem]) -> str:
    lines = [f"{_item_key(item)}: {item.title}" for item in items]
    return "Items:\n" + "\n".join(lines) + "\n\nCluster them per the rules."


def _item_key(item: RawItem) -> str:
    return f"{item.source}:{item.source_id}"


def _materialize(
    outputs: list[_ClusterOutput], items: list[RawItem]
) -> list[TopicCluster]:
    by_key = {_item_key(i): i for i in items}
    clusters: list[TopicCluster] = []
    for out in outputs:
        resolved = [by_key[iid] for iid in out.item_ids if iid in by_key]
        if not resolved:
            continue  # defensive; validator prevents this
        clusters.append(
            TopicCluster(
                topic=NormalizedTopic(
                    canonical_title=out.canonical_title,
                    one_line=out.one_line,
                    entities=out.entities,
                    tags=out.tags,
                ),
                items=resolved,
            )
        )
    return clusters


# --- Lexical fallback -----------------------------------------------------

def _lexical_cluster(items: list[RawItem]) -> list[TopicCluster]:
    """Greedy pairwise clustering: each item joins the highest-similarity
    existing cluster if that similarity >= _LEXICAL_THRESHOLD, else starts a
    new one. Comparison is against the cluster's representative (first item).

    Deliberately dumb — no fabricated entities/tags, no synthesized one-line.
    The picked representative's title becomes both canonical_title and one_line
    (truncated to the model limits). LLM path is where the real normalization
    happens; the fallback exists to keep the server operational.
    """
    clusters: list[list[RawItem]] = []
    for item in items:
        best_idx = -1
        best_sim = 0.0
        for idx, cluster in enumerate(clusters):
            sim = fuzz.token_set_ratio(item.title, cluster[0].title)
            if sim > best_sim:
                best_sim = sim
                best_idx = idx
        if best_sim >= _LEXICAL_THRESHOLD and best_idx >= 0:
            clusters[best_idx].append(item)
        else:
            clusters.append([item])

    result: list[TopicCluster] = []
    for members in clusters:
        # Representative: highest raw_score, tie-break by longer title.
        rep = max(members, key=lambda i: (i.raw_score, len(i.title)))
        title = " ".join(rep.title.strip().split())
        result.append(
            TopicCluster(
                topic=NormalizedTopic(
                    canonical_title=title[:80],
                    one_line=title[:160],
                    entities=[],
                    tags=[],
                ),
                items=members,
            )
        )
    return result
