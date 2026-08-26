"""End-to-end orchestrator for the trending pipeline.

    ingest → cluster → novelty-gate → score → cache → return

Kept out of server.py so tests can exercise it without constructing an MCP
request context. Server tools are thin wrappers over `run_trending_pipeline`.

Failure model:
- Per-source failures are captured in `source_health` (never raise here).
- Storage/LLM/etc failures during clustering/scoring propagate — the tool
  boundary in server.py catches them and returns structured errors.
"""
from __future__ import annotations

import uuid

import httpx

from trend_radar.adapters import build_adapters, fetch_all
from trend_radar.clustering import cluster_items
from trend_radar.config import AppSettings
from trend_radar.http import now_utc
from trend_radar.models import (
    RankedTopic,
    SourceName,
    TrendingResult,
    compute_topic_id,
)
from trend_radar.novelty import NoveltyGate
from trend_radar.obs import get_logger
from trend_radar.scoring import score_clusters
from trend_radar.storage import Storage

_LOG = get_logger("pipeline")


async def run_trending_pipeline(
    settings: AppSettings,
    storage: Storage,
    client: httpx.AsyncClient,
    *,
    lookback_hours: int,
    limit: int = 15,
    include_suppressed: bool = True,
    sources: list[SourceName] | None = None,
) -> TrendingResult:
    """Full pipeline. Returns the visible (filtered/limited) result; caches the full ranked set."""
    now = now_utc()

    # 1. Fetch — per-source failures land in source_health, never raise here.
    adapters = build_adapters(settings, client)
    if sources:
        wanted = set(sources)
        adapters = [a for a in adapters if a.name in wanted]
    fetch_results = await fetch_all(adapters, lookback_hours)
    all_items = [item for items, _ in fetch_results for item in items]
    source_health = [h for _, h in fetch_results]
    _LOG.info(
        "ingested %d items across %d sources (%d ok)",
        len(all_items),
        len(source_health),
        sum(1 for h in source_health if h.ok),
    )

    # 2. Cluster (LLM path with lexical fallback)
    clusters, method = await cluster_items(all_items, settings)
    _LOG.info("clustered into %d topics via %s", len(clusters), method)

    # 3. Novelty check — one ledger load, N cluster comparisons
    gate = NoveltyGate(settings.scoring, embed_model=settings.embed_model)
    ledger = await storage.recent_covered_records(settings.scoring.novelty_lookback_days)
    novelty_by_id: dict[str, tuple[float, bool, str | None]] = {}
    for c in clusters:
        hit = gate.compare(c.topic, ledger)
        if hit is None:
            continue
        mult, suppressed, reason = gate.multiplier(hit.similarity)
        # Only record non-default entries so unhit clusters use (1.0, False, None).
        if mult < 1.0 or suppressed:
            novelty_by_id[compute_topic_id(c.topic.canonical_title)] = (mult, suppressed, reason)

    # 4. Score — deterministic
    ranked = score_clusters(
        clusters, novelty_by_id, now, settings.scoring, settings.max_items_per_topic
    )

    # 5. Cache the *full* ranked set (so explain_ranking/mark_covered can
    #    reference any topic that was scored, not just visible ones).
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    full_result = TrendingResult(
        run_id=run_id,
        generated_at=now,
        lookback_hours=lookback_hours,
        clustering_method=method,
        source_health=source_health,
        topics=ranked,
    )
    await storage.save_run(full_result)
    await storage.prune_old_runs(keep=20)

    # 6. Return the filtered/limited view.
    visible: list[RankedTopic] = ranked if include_suppressed else [r for r in ranked if not r.suppressed]
    visible = visible[:limit]
    return full_result.model_copy(update={"topics": visible})
