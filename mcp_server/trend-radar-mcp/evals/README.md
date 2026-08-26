# trend-radar evals

Ten read-only evaluations that exercise multi-tool reasoning across the six trend-radar tools. Follows the [mclenhard/mcp-evals](https://github.com/mclenhard/mcp-evals) YAML schema — LLM-as-judge scoring against per-eval `expected_result` criteria.

## What's covered

| # | Eval | Tools exercised |
|---|---|---|
| 1 | `highest_corroboration_bonus` | `get_trending_topics` (+ aggregation) |
| 2 | `explain_top_scoring_topic` | `get_trending_topics` → `explain_ranking` |
| 3 | `novelty_check_ad_hoc` | `check_novelty` |
| 4 | `investigate_suppressed_topics` | `get_trending_topics` → `list_covered` |
| 5 | `top_arxiv_paper_explained` | `get_trending_topics(sources=[arxiv])` → `explain_ranking` |
| 6 | `cross_source_topics` | `get_trending_topics` (+ filtering) |
| 7 | `coverage_history_review` | `list_covered` |
| 8 | `diagnose_source_failure` | `get_trending_topics` → `get_source_config` |
| 9 | `persistent_vs_ephemeral` | `get_trending_topics` × 2 (different lookback_hours) |
| 10 | `highest_velocity_item_within_topic` | `get_trending_topics` → `explain_ranking` |

All ten are read-only — none call `mark_covered`, so running the suite doesn't mutate the ledger.

## Running

```bash
export ANTHROPIC_API_KEY=sk-...
npx mcp-evals \
  evals/trend-radar.evals.yaml \
  -- docker run --rm -i -v trend-radar-data:/data trend-radar:latest
```

The `--` separates the eval file from the server command. Any command that speaks MCP over stdio works — you can also point at the local Python entrypoint for development:

```bash
npx mcp-evals evals/trend-radar.evals.yaml -- uv run python -m trend_radar
```

## Interpreting results

Each eval prints a pass/fail plus the judge's rationale. Because scoring is LLM-based, expect some noise on borderline answers — re-running is cheap.

### What "pass" means here

The `expected_result` fields describe **shape and consistency**, not exact content. For example, eval #1 doesn't require a specific topic name — it requires:
- A topic named from the actual response
- A corroboration_bonus arithmetically consistent with distinct_sources
- The graceful "no cross-source coverage today" answer if that's genuinely the case

This is deliberate: trending data changes hourly. Hard-coding expected outputs would break the suite the moment a story cools off. The judge scores whether the assistant's reasoning followed the pipeline correctly.

## Requirements for a clean run

- No credentials required for any data source — HN, arXiv, and Hugging Face are all auth-free
- `ANTHROPIC_API_KEY` set on the *server side* enables `clustering_method=llm` in responses; without it, evals still pass with `lexical` clustering (canonical titles will be less clean)
- Ledger state affects evals #3, #4, #7. Fresh install = empty ledger = "no coverage" answers, which the judge accepts as correct per the expected_result wording.

## Adding new evals

Append to `evals:` in the YAML. Keep it read-only — the eval judge shouldn't be able to write to the ledger, or one run will pollute the next. If you need a write-exercising eval, run it in its own file with a scratch DB path (`TREND_RADAR_DB=/tmp/eval.db`).
