"""Manual smoke test: hits real HN, arXiv, and HF APIs and prints today's ranked topics.

Not run in CI. Use during development to verify the pipeline produces sensible
output against live data.

    uv run python scripts/smoke.py                  # last 24h, top 10
    uv run python scripts/smoke.py --lookback 6     # last 6h
    uv run python scripts/smoke.py --limit 5        # top 5 only
    uv run python scripts/smoke.py --sources hackernews arxiv   # skip HF

Uses a scratch SQLite DB so it doesn't touch your real ledger, and respects
ANTHROPIC_API_KEY if set (LLM clustering); otherwise falls back to lexical.
"""
from __future__ import annotations

import argparse
import asyncio
import tempfile
import time
from pathlib import Path

from trend_radar.config import AppSettings
from trend_radar.http import create_http_client
from trend_radar.models import SourceName
from trend_radar.pipeline import run_trending_pipeline


def _print_config(settings: AppSettings, lookback: int) -> None:
    print("Config:")
    if settings.has_anthropic_key():
        print(f"  llm_model = {settings.llm_model}")
    else:
        print("  llm_model = (no ANTHROPIC_API_KEY, falling back to lexical clustering)")
    print(f"  db_path   = {settings.db_path}  (scratch — real ledger untouched)")
    print(f"  lookback  = {lookback}h")
    print()


def _print_health(healths) -> None:
    print("Source health:")
    for h in healths:
        mark = "OK  " if h.ok else "FAIL"
        latency = f"{h.latency_ms}ms" if h.latency_ms is not None else "-"
        line = f"  [{mark}] {h.source:12}  items={h.items_fetched:3}  latency={latency}"
        if not h.ok:
            line += f"  error={h.error}"
        print(line)


def _print_topics(topics, limit: int) -> None:
    print()
    print(f"Top {min(len(topics), limit)} topics:")
    print("-" * 100)
    for i, t in enumerate(topics[:limit], 1):
        suppressed = " [SUPPRESSED]" if t.suppressed else ""
        print(f"{i:2}. {t.topic.canonical_title}{suppressed}")
        print(f"     {t.topic.one_line}")
        print(f"     score={t.score.final_score:.3f}  {t.score.explanation}")
        print(f"     distinct_sources={t.distinct_sources}  items={len(t.items)}")
        for si in t.items[:3]:
            title = si.item.title if len(si.item.title) <= 90 else si.item.title[:87] + "..."
            print(f"       - [{si.item.source:11}]  v={si.velocity:6.2f}  p={si.source_percentile:.2f}   {title}")
        print()


async def _run(lookback: int, limit: int, sources: list[SourceName] | None) -> int:
    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td)
        settings = AppSettings().model_copy(update={
            "db_path": scratch / "smoke.db",
            "log_dir": scratch,
        })
        _print_config(settings, lookback)

        started = time.monotonic()
        from trend_radar.storage import Storage
        async with Storage(settings.db_path) as storage, create_http_client() as client:
            result = await run_trending_pipeline(
                settings, storage, client,
                lookback_hours=lookback,
                limit=limit,
                sources=sources,
            )
        elapsed = time.monotonic() - started

        print(f"Run: {result.run_id}  clustering={result.clustering_method}  wall={elapsed:.1f}s")
        _print_health(result.source_health)
        _print_topics(result.topics, limit)

        if not result.topics:
            print("(No topics returned. If all source_health entries are ok=true but items_fetched=0,")
            print(" nothing crossed the threshold in the window — try a wider --lookback.)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lookback", type=int, default=24, help="Lookback in hours (default 24)")
    ap.add_argument("--limit", type=int, default=10, help="Max topics to display (default 10)")
    ap.add_argument(
        "--sources",
        nargs="+",
        choices=["hackernews", "arxiv", "huggingface"],
        default=None,
        help="Restrict to a subset; default = all three",
    )
    args = ap.parse_args()
    return asyncio.run(_run(args.lookback, args.limit, args.sources))


if __name__ == "__main__":
    raise SystemExit(main())
