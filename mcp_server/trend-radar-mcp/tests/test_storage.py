"""SQLite storage tests: migrations, ledger idempotency, run cache roundtrip."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from trend_radar.models import (
    CoveredTopic,
    NormalizedTopic,
    RankedTopic,
    RawItem,
    ScoreBreakdown,
    ScoredItem,
    SourceHealth,
    TrendingResult,
)
from trend_radar.storage import Storage


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "trend_radar.db"


class TestMigrations:
    @pytest.mark.asyncio
    async def test_fresh_db_creates_schema(self, db_path: Path) -> None:
        async with Storage(db_path) as s:
            cur = await s.conn.execute("PRAGMA user_version")
            version = (await cur.fetchone())[0]
            assert version >= 1

            # Both tables exist and are queryable
            await s.conn.execute("SELECT COUNT(*) FROM covered_topics")
            await s.conn.execute("SELECT COUNT(*) FROM runs")

    @pytest.mark.asyncio
    async def test_reconnect_is_noop(self, db_path: Path) -> None:
        async with Storage(db_path) as s:
            await s.conn.execute(
                "INSERT INTO covered_topics (topic_id, canonical_title, covered_on) "
                "VALUES (?, ?, ?)",
                ("a" * 12, "test", date.today().isoformat()),
            )
            await s.conn.commit()

        async with Storage(db_path) as s:
            cur = await s.conn.execute("SELECT COUNT(*) FROM covered_topics")
            assert (await cur.fetchone())[0] == 1

    @pytest.mark.asyncio
    async def test_conn_before_connect_raises(self, db_path: Path) -> None:
        s = Storage(db_path)
        with pytest.raises(RuntimeError, match="not connected"):
            _ = s.conn


class TestLedger:
    @pytest.mark.asyncio
    async def test_upsert_returns_true_for_new_row(self, db_path: Path) -> None:
        async with Storage(db_path) as s:
            entry = CoveredTopic(
                topic_id="a" * 12,
                canonical_title="Claude Opus 5 released",
                covered_on=date(2026, 8, 24),
            )
            assert await s.upsert_covered(entry) is True

    @pytest.mark.asyncio
    async def test_upsert_returns_false_on_second_write(self, db_path: Path) -> None:
        async with Storage(db_path) as s:
            entry = CoveredTopic(
                topic_id="a" * 12,
                canonical_title="First take",
                covered_on=date(2026, 8, 24),
            )
            assert await s.upsert_covered(entry) is True

            updated = entry.model_copy(update={"canonical_title": "Revised take"})
            assert await s.upsert_covered(updated) is False

            listed = await s.list_covered(days=30, limit=100)
            assert len(listed) == 1
            assert listed[0].canonical_title == "Revised take"

    @pytest.mark.asyncio
    async def test_one_line_preserved_on_update_without_one_line(self, db_path: Path) -> None:
        async with Storage(db_path) as s:
            entry = CoveredTopic(
                topic_id="a" * 12,
                canonical_title="t",
                covered_on=date(2026, 8, 24),
            )
            await s.upsert_covered(entry, one_line="original summary")
            # Update without providing one_line — COALESCE should retain old value
            await s.upsert_covered(entry.model_copy(update={"canonical_title": "t2"}))

            records = await s.recent_covered_records(days=30)
            assert records[0][2] == "original summary"

    @pytest.mark.asyncio
    async def test_list_covered_respects_days_cutoff(self, db_path: Path) -> None:
        async with Storage(db_path) as s:
            today = datetime.now(UTC).date()
            recent = CoveredTopic(
                topic_id="r" * 12, canonical_title="recent",
                covered_on=today - timedelta(days=5),
            )
            old = CoveredTopic(
                topic_id="o" * 12, canonical_title="old",
                covered_on=today - timedelta(days=100),
            )
            await s.upsert_covered(recent)
            await s.upsert_covered(old)

            recent_list = await s.list_covered(days=30, limit=100)
            assert {c.topic_id for c in recent_list} == {"r" * 12}

            wide_list = await s.list_covered(days=200, limit=100)
            assert len(wide_list) == 2

    @pytest.mark.asyncio
    async def test_list_covered_newest_first(self, db_path: Path) -> None:
        async with Storage(db_path) as s:
            today = datetime.now(UTC).date()
            for i, d in enumerate([today - timedelta(days=n) for n in (10, 2, 5)]):
                await s.upsert_covered(
                    CoveredTopic(
                        topic_id=f"{i:012d}",
                        canonical_title=f"day {d.isoformat()}",
                        covered_on=d,
                    )
                )
            listed = await s.list_covered(days=30, limit=10)
            dates = [c.covered_on for c in listed]
            assert dates == sorted(dates, reverse=True)


class TestRunsCache:
    def _sample_result(self, run_id: str = "run-1") -> TrendingResult:
        score = ScoreBreakdown(
            velocity=1.5, source_percentile=0.7, corroboration_bonus=0.15,
            novelty_multiplier=1.0, final_score=0.85, explanation="test",
        )
        topic = NormalizedTopic(canonical_title="t", one_line="o", entities=[], tags=[])
        item = RawItem(
            source="hackernews", source_id="x1", title="t",
            url="https://example.com/x1",
            permalink="https://news.ycombinator.com/item?id=x1",
            raw_score=10.0, comment_count=1,
            created_at=datetime(2026, 8, 24, 6, 0, tzinfo=UTC),
        )
        scored = ScoredItem(item=item, velocity=1.5, source_percentile=0.7)
        ranked = RankedTopic(
            topic_id="a" * 12, topic=topic, items=[scored],
            distinct_sources=1, score=score,
        )
        return TrendingResult(
            run_id=run_id,
            generated_at=datetime(2026, 8, 24, 12, 0, tzinfo=UTC),
            lookback_hours=24,
            clustering_method="lexical",
            source_health=[SourceHealth(source="hackernews", ok=True, items_fetched=5)],
            topics=[ranked],
        )

    @pytest.mark.asyncio
    async def test_save_and_get_roundtrip(self, db_path: Path) -> None:
        async with Storage(db_path) as s:
            original = self._sample_result()
            await s.save_run(original)
            fetched = await s.get_run("run-1")
            assert fetched is not None
            assert fetched.run_id == "run-1"
            assert fetched.topics[0].topic_id == "a" * 12
            assert fetched.topics[0].score.final_score == 0.85

    @pytest.mark.asyncio
    async def test_get_run_missing_returns_none(self, db_path: Path) -> None:
        async with Storage(db_path) as s:
            assert await s.get_run("does-not-exist") is None

    @pytest.mark.asyncio
    async def test_get_latest_run(self, db_path: Path) -> None:
        async with Storage(db_path) as s:
            r1 = self._sample_result("run-1")
            r2 = self._sample_result("run-2").model_copy(
                update={"generated_at": datetime(2026, 8, 25, 12, 0, tzinfo=UTC)}
            )
            await s.save_run(r1)
            await s.save_run(r2)
            latest = await s.get_latest_run()
            assert latest is not None
            assert latest.run_id == "run-2"

    @pytest.mark.asyncio
    async def test_prune_old_runs(self, db_path: Path) -> None:
        async with Storage(db_path) as s:
            for i in range(5):
                r = self._sample_result(f"run-{i}").model_copy(
                    update={"generated_at": datetime(2026, 8, 20 + i, 12, 0, tzinfo=UTC)}
                )
                await s.save_run(r)

            deleted = await s.prune_old_runs(keep=2)
            assert deleted == 3

            cur = await s.conn.execute("SELECT run_id FROM runs ORDER BY created_at DESC")
            rows = [r[0] for r in await cur.fetchall()]
            assert rows == ["run-4", "run-3"]
