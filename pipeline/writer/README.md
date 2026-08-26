# pipeline-writer

Stage 2 of the content pipeline. Takes trend-radar's `TrendingResult` JSON in, emits a `DraftBundle` (one narrative draft per non-suppressed topic) out. The renderer stage downstream turns each draft into a target format (Markdown for Apple Notes, blocks for Notion).

The writer does **compression, not research**. It only uses material already present in the trend-radar output — item titles, body excerpts, the deterministic score explanation. It never introduces numbers, dates, or entities from prior knowledge.

## Install

```bash
cd pipeline/writer
uv sync --extra dev
```

## Run

```bash
# Piped from trend-radar (once trend-radar has a CLI):
trend-radar get_trending_topics | uv run python -m writer

# Or from a captured JSON file:
uv run python -m writer --input trending.json --output drafts.json

# Dry-run — no LLM call, useful for pipeline wiring tests:
uv run python -m writer --input trending.json --dry-run

# First N topics only:
uv run python -m writer -i trending.json -n 5

# Include topics suppressed by the novelty gate (default: skipped):
uv run python -m writer -i trending.json --include-suppressed
```

Set `ANTHROPIC_API_KEY` in the environment for real runs. Override the model with `WRITER_MODEL` (default `anthropic:claude-sonnet-4-6`) or `--model`.

## Contract

- **Input:** `TrendingResultIn` — subset of `trend_radar.models.TrendingResult`. Extra fields are ignored, so trend-radar can add fields without breaking the writer.
- **Output:** `DraftBundle` — see `src/writer/models.py`. Each `Draft` carries `topic_id` (echoed from trend-radar for later `mark_covered`), a headline/subhead, ~150-300 words of `body_md`, key signals, sourced items, and the **verbatim** `ranking_rationale` copied from `score.explanation`.
- **Guarantee:** `topic_id`, `canonical_title`, and `ranking_rationale` are overwritten on the model's output — the writer refuses to let the model paraphrase them, no matter what the prompt produces.

## Tests

```bash
uv run pytest
```

Scaffold tests cover model parsing, prompt rendering, and the `--dry-run` end-to-end path. A live LLM test is stubbed with `@pytest.mark.skip`; enable it once you're iterating on the prompt.

## What's next

- **Prompt iteration.** The system prompt in `agent.py` is a starting draft. Expect to tighten it once you see live output — especially the "thin coverage" escape hatch and the 150-300 word discipline.
- **Batch concurrency.** Currently drafts topics sequentially. `asyncio.gather` with a small semaphore is a one-line change if throughput matters.
- **Renderer.** Sibling package `pipeline/renderer/` (not yet built) — should consume a `DraftBundle` and emit whatever the terminal stage (`apple-notes` MCP, Notion) accepts.
