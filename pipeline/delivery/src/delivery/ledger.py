"""SQLite delivery ledger.

Tracks (run_id, topic_id, target) → external_id so re-running the same
bundle skips already-delivered pieces. Deliberately dumb: one table,
synchronous, no migrations. If schema needs to evolve, drop the file —
it's a cache of what already happened, not source of truth.

Source of truth for "was this covered?" lives elsewhere (trend-radar's
novelty ledger). This one prevents same-bundle re-delivery, which is a
different concern — a same-day retry after a transient bridge failure
shouldn't double-post.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from delivery.models import DeliveryRecord, DeliveryStatus, DeliveryTarget

SCHEMA = """
CREATE TABLE IF NOT EXISTS deliveries (
    run_id       TEXT NOT NULL,
    topic_id     TEXT NOT NULL,
    target       TEXT NOT NULL,
    status       TEXT NOT NULL,
    external_id  TEXT,
    delivered_at TEXT,
    meta         TEXT,
    PRIMARY KEY (run_id, topic_id, target)
);
"""


class Ledger:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Ledger":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get(self, run_id: str, topic_id: str, target: DeliveryTarget) -> DeliveryRecord | None:
        row = self.conn.execute(
            "SELECT run_id, topic_id, target, status, external_id, delivered_at, meta "
            "FROM deliveries WHERE run_id = ? AND topic_id = ? AND target = ?",
            (run_id, topic_id, target),
        ).fetchone()
        if row is None:
            return None
        return DeliveryRecord(
            run_id=row[0],
            topic_id=row[1],
            target=row[2],
            status=row[3],  # type: ignore[arg-type]
            external_id=row[4],
            delivered_at=datetime.fromisoformat(row[5]) if row[5] else None,
            meta=json.loads(row[6]) if row[6] else {},
        )

    def record(
        self,
        run_id: str,
        topic_id: str,
        target: DeliveryTarget,
        status: DeliveryStatus,
        external_id: str | None = None,
        meta: dict | None = None,
    ) -> DeliveryRecord:
        """Insert or update a delivery outcome. Returns the resulting record."""
        now = datetime.now(timezone.utc)
        rec = DeliveryRecord(
            run_id=run_id,
            topic_id=topic_id,
            target=target,
            status=status,
            external_id=external_id,
            delivered_at=now if status == "delivered" else None,
            meta=meta or {},
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO deliveries "
            "(run_id, topic_id, target, status, external_id, delivered_at, meta) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                topic_id,
                target,
                status,
                external_id,
                rec.delivered_at.isoformat() if rec.delivered_at else None,
                json.dumps(meta or {}),
            ),
        )
        self.conn.commit()
        return rec
