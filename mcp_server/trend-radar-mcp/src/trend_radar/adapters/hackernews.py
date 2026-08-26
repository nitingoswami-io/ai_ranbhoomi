"""Hacker News adapter — Algolia search API, keyword-fan-out.

Five parallel term-searches per fetch, dedup by objectID. The
`numericFilters=points>20,created_at_i>=cutoff` server-side filter keeps
the payload small — we don't need to page beyond the first 50 hits per
term for a 24-hour window.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import ClassVar

from trend_radar.adapters.base import SourceAdapter
from trend_radar.models import RawItem, SourceName

_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
_HN_ITEM_URL = "https://news.ycombinator.com/item?id="

# Broad enough to catch adjacent stories (agents, reasoning, tooling)
# without pulling every "AI-adjacent" fluff piece.
AI_QUERIES: tuple[str, ...] = ("AI", "LLM", "GPT", "Claude", "agent")


class HackerNewsAdapter(SourceAdapter):
    name: ClassVar[SourceName] = "hackernews"

    async def fetch(self, lookback_hours: int) -> list[RawItem]:
        cutoff_ts = int((self._now() - timedelta(hours=lookback_hours)).timestamp())
        results = await asyncio.gather(*(self._search(q, cutoff_ts) for q in AI_QUERIES))
        merged: dict[str, RawItem] = {}
        for hits in results:
            for item in hits:
                merged.setdefault(item.source_id, item)
        return list(merged.values())

    async def _search(self, query: str, cutoff_ts: int) -> list[RawItem]:
        await self.limiter.acquire()
        resp = await self.client.get(
            _SEARCH_URL,
            params={
                "query": query,
                "tags": "story",
                "numericFilters": f"points>20,created_at_i>{cutoff_ts}",
                "hitsPerPage": 50,
            },
        )
        resp.raise_for_status()
        return [_hit_to_item(hit) for hit in resp.json().get("hits", []) if hit.get("title")]


def _hit_to_item(hit: dict) -> RawItem:
    oid = str(hit["objectID"])
    permalink = f"{_HN_ITEM_URL}{oid}"
    url = hit.get("url") or permalink
    body = (hit.get("story_text") or "").strip()
    return RawItem(
        source="hackernews",
        source_id=oid,
        title=hit["title"],
        url=url,
        permalink=permalink,
        raw_score=float(hit.get("points") or 0),
        comment_count=hit.get("num_comments"),
        created_at=datetime.fromtimestamp(hit["created_at_i"], tz=UTC),
        body_excerpt=body[:500] if body else None,
    )
