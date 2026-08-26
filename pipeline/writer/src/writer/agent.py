"""Pydantic AI agent that composes a Draft from one RankedTopic.

The agent's only authorized inputs are the fields the caller renders into
the prompt: topic name, one-liner, entities, ranked items with their
scores, and the deterministic score explanation from trend-radar. It
must not introduce numbers, dates, or entities not present in that
material — the writer's job is compression, not research.
"""
from __future__ import annotations

import os

from pydantic_ai import Agent

from writer.models import Draft, RankedTopicIn

DEFAULT_MODEL = os.environ.get("WRITER_MODEL", "anthropic:claude-sonnet-4-6")

SYSTEM_PROMPT = """\
You compose short narrative drafts for a daily AI/ML briefing.

You will receive ONE topic and the ranked items that produced its score.
Your job is to turn that into a Draft the renderer can format.

Non-negotiable rules:

- Use ONLY the provided material. Never introduce numbers, dates,
  organizations, or events not present in the topic name, one-liner,
  entities, item titles, item body excerpts, or the ranking numbers.
- `body_md` is 150-300 words of prose. Open with what changed or why
  attention is on this now. Cite specific items only where the item
  actually adds signal (e.g. "the arXiv preprint...", "the HN thread
  with 428 points"). Close with one sentence on what to watch next —
  only if the material supports it; otherwise omit.
- `key_signals` is 2-4 short bullets naming concrete evidence: which
  sources corroborated, what the scores were, which items pulled
  weight. These are the "why does this rank" details, not opinions.
- `sources` lists each ranked item with a short `signal` line stating
  its evidence (e.g. "428 points on Hacker News", "top-quintile
  velocity on arXiv"). Derive the signal from the numbers you were
  given, not from prior knowledge.
- `ranking_rationale` will be overwritten by the caller with the
  trend-radar score explanation verbatim; you may leave it empty or
  echo what you saw — it will not be used.
- If the material is too thin to write 150 words without inventing
  content, make `body_md` exactly: "Thin coverage — not enough signal
  to draft. Consider skipping." That is a valid draft.
"""


def build_agent(model: str = DEFAULT_MODEL) -> Agent[None, Draft]:
    agent: Agent[None, Draft] = Agent(
        model=model,
        output_type=Draft,
        system_prompt=SYSTEM_PROMPT,
        retries=2,
    )
    return agent


def render_topic_prompt(rt: RankedTopicIn) -> str:
    """Format one RankedTopic as the agent's user prompt.

    Plain text (not JSON) so the model reads it as a briefing to react to,
    not as a schema to echo. All numeric grounding is spelled out.
    """
    lines: list[str] = [
        f"Topic ID: {rt.topic_id}",
        f"Canonical title: {rt.topic.canonical_title}",
        f"One-liner: {rt.topic.one_line}",
    ]
    if rt.topic.entities:
        lines.append(f"Entities: {', '.join(rt.topic.entities)}")
    if rt.topic.tags:
        lines.append(f"Tags: {', '.join(rt.topic.tags)}")
    lines += [
        f"Distinct sources: {rt.distinct_sources}",
        "",
        "Score breakdown:",
        f"  velocity            = {rt.score.velocity:.3f}",
        f"  source_percentile   = {rt.score.source_percentile:.3f}",
        f"  corroboration_bonus = {rt.score.corroboration_bonus:.3f}",
        f"  novelty_multiplier  = {rt.score.novelty_multiplier:.3f}",
        f"  final_score         = {rt.score.final_score:.3f}",
        f"  explanation         = {rt.score.explanation}",
        "",
        "Ranked items (sole source of factual grounding):",
    ]
    for i, si in enumerate(rt.items, 1):
        it = si.item
        lines.append(f"  {i}. [{it.source}] {it.title}")
        lines.append(f"     url: {it.url}")
        if it.permalink:
            lines.append(f"     discussion: {it.permalink}")
        lines.append(
            f"     raw_score={it.raw_score:.0f} "
            f"velocity={si.velocity:.3f} "
            f"source_percentile={si.source_percentile:.3f}"
        )
        if it.comment_count is not None:
            lines.append(f"     comments: {it.comment_count}")
        if it.body_excerpt:
            lines.append(f"     excerpt: {it.body_excerpt}")

    lines += ["", "Emit a Draft that follows the rules in the system prompt."]
    return "\n".join(lines)


async def write_draft(
    rt: RankedTopicIn,
    *,
    agent: Agent[None, Draft] | None = None,
) -> Draft:
    """Compose one Draft for one RankedTopic.

    Overwrites `topic_id`, `canonical_title`, and `ranking_rationale` on
    the model's output so those three fields are guaranteed to match the
    upstream contract regardless of what the model produced.
    """
    if agent is None:
        agent = build_agent()
    result = await agent.run(render_topic_prompt(rt))
    return result.output.model_copy(update={
        "topic_id": rt.topic_id,
        "canonical_title": rt.topic.canonical_title,
        "ranking_rationale": rt.score.explanation,
    })
