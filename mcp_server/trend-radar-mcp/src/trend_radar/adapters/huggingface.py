"""Hugging Face Daily Papers adapter — /api/daily_papers?date=YYYY-MM-DD, one call per day in the lookback window.

Endpoint is undocumented but stable. For lookback > 24h we page backwards
day-by-day; 404 means "no papers that day" and is treated as empty, not an
error. Dedup on paper.id (same paper can appear on multiple days).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import ClassVar

from trend_radar.adapters.base import SourceAdapter
from trend_radar.models import RawItem, SourceName

_API_URL = "https://huggingface.co/api/daily_papers"


class HuggingFaceAdapter(SourceAdapter):
    name: ClassVar[SourceName] = "huggingface"

    async def fetch(self, lookback_hours: int) -> list[RawItem]:
        cutoff = self._now() - timedelta(hours=lookback_hours)
        # Ceil to whole days; the endpoint is date-indexed.
        days_back = max(1, (lookback_hours + 23) // 24)
        today = self._now().date()
        seen: dict[str, RawItem] = {}
        for i in range(days_back):
            d = today - timedelta(days=i)
            for item in await self._fetch_day(d):
                if item.created_at < cutoff:
                    continue
                seen.setdefault(item.source_id, item)
        return list(seen.values())

    async def _fetch_day(self, day) -> list[RawItem]:
        await self.limiter.acquire()
        resp = await self.client.get(_API_URL, params={"date": day.isoformat()})
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        payload = resp.json()
        return [item for item in (_entry_to_item(e) for e in payload) if item is not None]


def _entry_to_item(entry: dict) -> RawItem | None:
    """Convert one Daily Papers entry to a RawItem.

    For the timestamp we prefer `paper.submittedOnDailyAt` — when HF's curators
    featured the paper — because HF Daily Papers routinely surfaces papers that
    were published 1–2 days earlier on arXiv. Using the paper's original
    publish date would filter most entries out of a 24h lookback window.
    Fall back to publishedAt fields if the submission timestamp is missing
    (older responses / edge cases).
    """
    paper = entry.get("paper") or {}
    pid = paper.get("id")
    if not pid:
        return None
    published_str = (
        paper.get("submittedOnDailyAt")
        or entry.get("publishedAt")
        or paper.get("publishedAt")
    )
    if not published_str:
        return None
    try:
        published = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
    except ValueError:
        return None
    if published.tzinfo is None:
        published = published.replace(tzinfo=UTC)
    title = (paper.get("title") or "").strip()
    if not title:
        return None
    summary = (paper.get("summary") or "").strip()
    return RawItem(
        source="huggingface",
        source_id=str(pid),
        title=title,
        url=f"https://huggingface.co/papers/{pid}",
        permalink=None,
        raw_score=float(paper.get("upvotes") or 0),
        comment_count=None,
        created_at=published,
        body_excerpt=summary[:500] if summary else None,
    )
