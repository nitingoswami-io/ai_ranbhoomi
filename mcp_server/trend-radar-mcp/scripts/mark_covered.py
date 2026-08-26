"""Close the novelty loop — read a DeliveryReport on stdin, mark each
delivered topic as covered in the trend-radar ledger.

Input: JSON matching pipeline/delivery's DeliveryReport shape (run_id,
target, dry_run, records[]). Records with status != 'delivered' are
counted but not written (skipped/failed/dry-run don't count as
coverage).

Resolution: canonical_title and one_line come from the latest cached
trend-radar run (the same fetch that produced the topic_ids). If a
delivered topic_id isn't in that run — shouldn't happen unless the
ledger was cleared between fetch and delivery — we skip with a reason
rather than fabricate a title.

Output: JSON summary on stdout, one entry per delivery record.
Idempotent — running the same DeliveryReport twice produces the same
final ledger state (upsert on topic_id), and `was_new=False` on the
second call.

    ... | uv run python scripts/mark_covered.py                # write
    ... | uv run python scripts/mark_covered.py --dry-run      # inspect
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from pydantic import BaseModel, ConfigDict

from trend_radar.http import now_utc
from trend_radar.models import CoveredTopic
from trend_radar.config import get_settings
from trend_radar.storage import Storage

_lax = ConfigDict(extra="ignore")


class _DeliveryRecordIn(BaseModel):
    model_config = _lax
    topic_id: str
    target: str
    status: str
    external_id: str | None = None


class _DeliveryReportIn(BaseModel):
    model_config = _lax
    run_id: str
    target: str
    dry_run: bool = False
    records: list[_DeliveryRecordIn]


class _CoveredEntry(BaseModel):
    topic_id: str
    canonical_title: str | None = None
    marked: bool = False
    was_new: bool | None = None
    reason: str | None = None


async def _run(dry_run: bool) -> int:
    report = _DeliveryReportIn.model_validate_json(sys.stdin.read())
    settings = get_settings()

    entries: list[_CoveredEntry] = []

    async with Storage(settings.db_path) as storage:
        run = await storage.get_latest_run()
        by_id = {t.topic_id: t for t in (run.topics if run else [])}

        for rec in report.records:
            if rec.status != "delivered":
                entries.append(_CoveredEntry(
                    topic_id=rec.topic_id,
                    reason=f"skipped: delivery status was {rec.status!r}, not 'delivered'",
                ))
                continue

            cached = by_id.get(rec.topic_id)
            if cached is None:
                entries.append(_CoveredEntry(
                    topic_id=rec.topic_id,
                    reason="skipped: topic_id not in the latest cached trend-radar run",
                ))
                continue

            title = cached.topic.canonical_title
            one_line = cached.topic.one_line

            if dry_run:
                entries.append(_CoveredEntry(
                    topic_id=rec.topic_id,
                    canonical_title=title,
                    reason="dry-run: ledger not written",
                ))
                continue

            entry = CoveredTopic(
                topic_id=rec.topic_id,
                canonical_title=title,
                covered_on=now_utc().date(),
                post_url=None,  # Apple Notes has no URL scheme worth recording
                notes=f"{rec.target}:{rec.external_id}" if rec.external_id else rec.target,
            )
            was_new = await storage.upsert_covered(entry, one_line=one_line)
            entries.append(_CoveredEntry(
                topic_id=rec.topic_id,
                canonical_title=title,
                marked=True,
                was_new=was_new,
            ))

    marked = sum(1 for e in entries if e.marked)
    new = sum(1 for e in entries if e.was_new)
    skipped = sum(1 for e in entries if not e.marked)
    effective_dry_run = dry_run or report.dry_run

    print(
        f"mark_covered: {marked} marked ({new} new), {skipped} skipped"
        + (" [dry-run]" if effective_dry_run else ""),
        file=sys.stderr,
    )

    out = {
        "run_id": report.run_id,
        "dry_run": effective_dry_run,
        "counts": {"marked": marked, "new_rows": new, "skipped": skipped},
        "entries": [e.model_dump() for e in entries],
    }
    sys.stdout.write(json.dumps(out, indent=2) + "\n")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--dry-run", action="store_true",
        help="Read the report but do not write to the trend-radar ledger.",
    )
    args = p.parse_args()
    return asyncio.run(_run(dry_run=args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
