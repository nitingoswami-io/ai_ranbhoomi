"""Pydantic AI agent that composes a Draft from one RankedTopic.

The agent's only authorized inputs are the fields the caller renders into
the prompt: topic name, one-liner, entities, ranked items with their
scores, and the deterministic score explanation from trend-radar. It
must not introduce numbers, dates, or entities not present in that
material — the writer's job is compression, not research.

Word-count discipline is enforced two ways: the prompt names an explicit
range and per-paragraph budgets, and an output_validator on the agent
retries the model with a specific correction message if it comes in too
short or too long.
"""
from __future__ import annotations

import os

from pydantic_ai import Agent, ModelRetry, RunContext

from writer.models import Draft, RankedTopicIn

DEFAULT_MODEL = os.environ.get("WRITER_MODEL", "anthropic:claude-sonnet-4-6")

# body_md target range. The upper bound is the real discipline — 220 forces
# cuts of meta-commentary and hedges. The lower bound stays generous (150) so
# thin topics (few items, terse excerpts) don't get padded to hit a minimum.
# The 'Thin coverage' escape hatch below is the pressure release when even
# 150 words would require invention.
MIN_WORDS = 150
MAX_WORDS = 220
THIN_COVERAGE_SIGNAL = "Thin coverage"

SYSTEM_PROMPT = f"""\
You compose short narrative drafts for a daily AI/ML briefing.

You will receive ONE topic and the ranked items that produced its score.
Turn that into a Draft the renderer can format.

Non-negotiable rules:

- Use ONLY the provided material. Never introduce numbers, dates,
  organizations, or events not present in the topic name, one-liner,
  entities, item titles, item body excerpts, or the ranking numbers.

- `body_md` is {MIN_WORDS}-{MAX_WORDS} words in 2-3 short paragraphs.
  Structure:
    * Opening (~60-90 words): what changed or why attention is on this
      now. Anchor to specific evidence (points, velocity, sources) in
      the first two sentences.
    * Middle (~80-120 words): the strongest supporting item, and any
      cross-source corroboration. Name items concretely ("the HN
      thread with 428 points", "the arXiv preprint titled X").
    * Optional close (one short sentence, ≤25 words): a concrete open
      question the material itself raises. Omit if forced.

  Cut any sentence that could be dropped without losing evidence.
  Do NOT write meta-commentary about what the convergence "means" or
  "signals" — the evidence IS the story. Do NOT hedge ("it's worth
  noting", "interestingly", "notably"). Prefer active voice and short
  sentences.

- `key_signals` is 2-4 short bullets naming concrete evidence: which
  sources corroborated, what the scores were, which items pulled
  weight. Not opinions.

- `sources` lists each ranked item with a short `signal` line stating
  its evidence (e.g. "428 points on Hacker News", "top-quintile
  velocity on arXiv"). Derive from the numbers you were given.

- `ranking_rationale` is overwritten by the caller — leave it empty
  or echo what you saw.

- If the material is too thin to write {MIN_WORDS} words without
  inventing content, make `body_md` exactly: "{THIN_COVERAGE_SIGNAL} —
  not enough signal to draft. Consider skipping." That is a valid
  draft; do NOT pad to reach the target.
"""


def _word_count_error(body_md: str) -> str | None:
    """Return a correction message if body_md is out of range, else None.

    The "thin coverage" escape hatch is exempt — a short refusal is a
    valid outcome and shouldn't be padded to hit the minimum.

    Split off from the validator so tests can exercise the rule directly
    without any pydantic-ai machinery.
    """
    body = body_md.strip()
    if body.startswith(THIN_COVERAGE_SIGNAL):
        return None
    n = len(body.split())
    if n < MIN_WORDS:
        return (
            f"body_md is {n} words; target is {MIN_WORDS}-{MAX_WORDS}. "
            f"Add at least {MIN_WORDS - n} more words of substance — do NOT pad. "
            "Either surface concrete evidence you haven't used yet (item titles, "
            "scores, cross-source overlap), or, if the material is genuinely too "
            "thin, use the exact 'Thin coverage' escape from the system prompt."
        )
    if n > MAX_WORDS:
        return (
            f"body_md is {n} words; cap is {MAX_WORDS}. Cut at least {n - MAX_WORDS} words. "
            "Drop meta-commentary, hedges, and any sentence that could be removed "
            "without losing a piece of evidence. Preserve the concrete-evidence lines."
        )
    return None


def build_agent(model: str = DEFAULT_MODEL) -> Agent[None, Draft]:
    agent: Agent[None, Draft] = Agent(
        model=model,
        output_type=Draft,
        system_prompt=SYSTEM_PROMPT,
        retries=2,
    )

    @agent.output_validator
    def _enforce_word_count(_ctx: RunContext[None], output: Draft) -> Draft:
        err = _word_count_error(output.body_md)
        if err:
            raise ModelRetry(err)
        return output

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
