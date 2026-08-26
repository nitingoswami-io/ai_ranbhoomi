"""arXiv adapter — Atom feed, cs.AI/cs.LG/cs.CL, sorted by submission date desc.

Single request per fetch. Papers have no score/comment signal, so raw_score
is 0 and comment_count is None — the scoring engine will assign a baseline
percentile (§3) and let corroboration do the ranking work.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import ClassVar

import feedparser  # type: ignore[import-untyped]

from trend_radar.adapters.base import SourceAdapter
from trend_radar.models import RawItem, SourceName

_API_URL = "http://export.arxiv.org/api/query"
_CATEGORIES = "cat:cs.AI OR cat:cs.LG OR cat:cs.CL"


class ArxivAdapter(SourceAdapter):
    name: ClassVar[SourceName] = "arxiv"

    async def fetch(self, lookback_hours: int) -> list[RawItem]:
        cutoff = self._now().replace(microsecond=0) - timedelta(hours=lookback_hours)
        await self.limiter.acquire()
        resp = await self.client.get(
            _API_URL,
            params={
                "search_query": _CATEGORIES,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
                "max_results": 100,
            },
        )
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)

        items: list[RawItem] = []
        for entry in feed.entries:
            published = _parse_iso(entry.get("published"))
            if published is None:
                continue
            # Entries are descending — first one below cutoff means we're done.
            if published < cutoff:
                break
            items.append(_entry_to_item(entry, published))
        return items


def _entry_to_item(entry: object, published: datetime) -> RawItem:
    # feedparser attribute access, not dict lookup
    entry_id: str = entry.id  # type: ignore[attr-defined]
    arxiv_id = entry_id.rsplit("/", 1)[-1]  # "2401.12345v1"
    title = (entry.title or "").strip().replace("\n", " ")  # type: ignore[attr-defined]
    summary = (getattr(entry, "summary", "") or "").strip().replace("\n", " ")
    return RawItem(
        source="arxiv",
        source_id=arxiv_id,
        title=title,
        url=entry_id,
        permalink=None,
        raw_score=0.0,
        comment_count=None,
        created_at=published,
        body_excerpt=summary[:500] if summary else None,
    )


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    # arXiv publishes as "2026-01-15T10:00:00Z". fromisoformat handles this in 3.11+.
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


