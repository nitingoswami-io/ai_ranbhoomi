"""SQLite storage: novelty ledger + run cache.

Two tables:
- covered_topics — the novelty ledger. topic_id is primary key so mark_covered
  is idempotent.
- runs — a per-run cache of the full TrendingResult, gzipped JSON in a BLOB.
  Powers explain_ranking without re-ingesting.

Schema versioning via PRAGMA user_version, stepped through the _MIGRATIONS
tuple. To add a table or column, append a new SQL block.
"""
from __future__ import annotations

import gzip
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import TracebackType

import aiosqlite

from trend_radar.models import CoveredTopic, TrendingResult

# One SQL block per version. Order matters — never reorder or edit an
# existing entry; only append. Each is run inside a transaction.
_MIGRATIONS: tuple[str, ...] = (
    # v1 — initial schema
    """
    CREATE TABLE covered_topics (
        topic_id        TEXT PRIMARY KEY,
        canonical_title TEXT NOT NULL,
        one_line        TEXT,
        covered_on      TEXT NOT NULL,   -- ISO date
        post_url        TEXT,
        notes           TEXT,
        embedding       BLOB
    );
    CREATE INDEX ix_covered_on ON covered_topics(covered_on);

    CREATE TABLE runs (
        run_id     TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,        -- ISO datetime UTC
        payload    BLOB NOT NULL         -- gzipped JSON of TrendingResult
    );
    CREATE INDEX ix_runs_created ON runs(created_at DESC);
    """,
)


class Storage:
    """Async SQLite wrapper. Use as an async context manager."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def __aenter__(self) -> Storage:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def connect(self) -> None:
        if self._conn is not None:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._migrate()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Storage is not connected. Use `async with storage:` or call connect().")
        return self._conn

    async def _migrate(self) -> None:
        cur = await self.conn.execute("PRAGMA user_version")
        row = await cur.fetchone()
        version = int(row[0]) if row else 0
        for i, sql in enumerate(_MIGRATIONS[version:], start=version):
            await self.conn.executescript(sql)
            # user_version PRAGMA doesn't accept parameter binding.
            await self.conn.execute(f"PRAGMA user_version = {i + 1}")
        await self.conn.commit()

    # --- Ledger --------------------------------------------------------

    async def upsert_covered(
        self, entry: CoveredTopic, *, one_line: str | None = None
    ) -> bool:
        """Insert or update. Returns True if this was a new row, False if it updated.

        `one_line` is stored separately since CoveredTopic doesn't carry it —
        it's populated when we resolve topic_id via the run cache, and aids
        the rapidfuzz novelty match.
        """
        cur = await self.conn.execute(
            "SELECT 1 FROM covered_topics WHERE topic_id = ?", (entry.topic_id,)
        )
        existed = (await cur.fetchone()) is not None
        await self.conn.execute(
            """
            INSERT INTO covered_topics (topic_id, canonical_title, one_line, covered_on, post_url, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(topic_id) DO UPDATE SET
                canonical_title = excluded.canonical_title,
                one_line        = COALESCE(excluded.one_line, covered_topics.one_line),
                covered_on      = excluded.covered_on,
                post_url        = excluded.post_url,
                notes           = excluded.notes
            """,
            (
                entry.topic_id,
                entry.canonical_title,
                one_line,
                entry.covered_on.isoformat(),
                str(entry.post_url) if entry.post_url else None,
                entry.notes,
            ),
        )
        await self.conn.commit()
        return not existed

    async def list_covered(self, days: int, limit: int) -> list[CoveredTopic]:
        cutoff = (datetime.now(UTC).date() - timedelta(days=days)).isoformat()
        cur = await self.conn.execute(
            "SELECT topic_id, canonical_title, covered_on, post_url, notes "
            "FROM covered_topics WHERE covered_on >= ? "
            "ORDER BY covered_on DESC LIMIT ?",
            (cutoff, limit),
        )
        rows = await cur.fetchall()
        return [
            CoveredTopic(
                topic_id=r[0],
                canonical_title=r[1],
                covered_on=date.fromisoformat(r[2]),
                post_url=r[3] if r[3] else None,
                notes=r[4],
            )
            for r in rows
        ]

    async def recent_covered_records(self, days: int) -> list[tuple[str, str, str, date]]:
        """(topic_id, canonical_title, one_line, covered_on) for novelty checks."""
        cutoff = (datetime.now(UTC).date() - timedelta(days=days)).isoformat()
        cur = await self.conn.execute(
            "SELECT topic_id, canonical_title, one_line, covered_on "
            "FROM covered_topics WHERE covered_on >= ?",
            (cutoff,),
        )
        rows = await cur.fetchall()
        return [(r[0], r[1], r[2] or "", date.fromisoformat(r[3])) for r in rows]

    # --- Runs cache ----------------------------------------------------

    async def save_run(self, result: TrendingResult) -> None:
        payload = gzip.compress(result.model_dump_json().encode("utf-8"))
        await self.conn.execute(
            "INSERT OR REPLACE INTO runs (run_id, created_at, payload) VALUES (?, ?, ?)",
            (result.run_id, result.generated_at.isoformat(), payload),
        )
        await self.conn.commit()

    async def get_run(self, run_id: str) -> TrendingResult | None:
        cur = await self.conn.execute("SELECT payload FROM runs WHERE run_id = ?", (run_id,))
        row = await cur.fetchone()
        if not row:
            return None
        return TrendingResult.model_validate_json(gzip.decompress(row[0]))

    async def get_latest_run(self) -> TrendingResult | None:
        cur = await self.conn.execute(
            "SELECT payload FROM runs ORDER BY created_at DESC LIMIT 1"
        )
        row = await cur.fetchone()
        if not row:
            return None
        return TrendingResult.model_validate_json(gzip.decompress(row[0]))

    async def prune_old_runs(self, keep: int = 20) -> int:
        """Trim the run cache to the last `keep` runs. Returns rows deleted."""
        cur = await self.conn.execute("SELECT COUNT(*) FROM runs")
        total = int((await cur.fetchone())[0])
        if total <= keep:
            return 0
        await self.conn.execute(
            "DELETE FROM runs WHERE run_id NOT IN "
            "(SELECT run_id FROM runs ORDER BY created_at DESC LIMIT ?)",
            (keep,),
        )
        await self.conn.commit()
        return total - keep
