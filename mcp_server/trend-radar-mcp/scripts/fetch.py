"""Headless fetcher for trend-radar. Prints TrendingResult JSON to stdout.

Emits the same shape the MCP tool `get_trending_topics` returns, so the
downstream pipeline (writer → renderer → delivery) consumes it
identically. Meant for cron/launchd — reads config from environment
(TREND_RADAR_DB, ANTHROPIC_API_KEY, etc.), writes to the real ledger.

    uv run python scripts/fetch.py                    # 24h, top 15
    uv run python scripts/fetch.py --lookback 12 -n 5
    uv run python scripts/fetch.py --include-suppressed
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from trend_radar.config import get_settings
from trend_radar.http import create_http_client
from trend_radar.obs import setup_logging
from trend_radar.pipeline import run_trending_pipeline
from trend_radar.storage import Storage


async def _run(lookback_hours: int, limit: int, include_suppressed: bool) -> None:
    settings = get_settings()
    setup_logging(settings)
    print(
        f"trend-radar: db={settings.db_path} lookback={lookback_hours}h limit={limit}",
        file=sys.stderr,
    )
    async with Storage(settings.db_path) as storage, create_http_client() as client:
        result = await run_trending_pipeline(
            settings,
            storage,
            client,
            lookback_hours=lookback_hours,
            limit=limit,
            include_suppressed=include_suppressed,
            sources=None,
        )

    sys.stdout.write(result.model_dump_json(indent=2))
    sys.stdout.write("\n")

    kept = sum(1 for t in result.topics if not t.suppressed)
    print(
        f"trend-radar: run_id={result.run_id} clustering={result.clustering_method} "
        f"topics={len(result.topics)} non-suppressed={kept}",
        file=sys.stderr,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lookback-hours", type=int, default=24, help="Lookback window in hours (default 24).")
    ap.add_argument("--limit", "-n", type=int, default=15, help="Max topics to return (default 15).")
    ap.add_argument(
        "--include-suppressed", action="store_true",
        help="Include topics already covered per the novelty ledger.",
    )
    args = ap.parse_args()
    asyncio.run(_run(args.lookback_hours, args.limit, args.include_suppressed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
