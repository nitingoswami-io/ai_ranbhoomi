"""Token-bucket unit tests with an injected clock — no real time elapses."""
from __future__ import annotations

from collections.abc import Callable

import pytest

from trend_radar.http import TokenBucket


class FakeClock:
    """Deterministic monotonic clock. `sleep(dt)` advances time; no real wait."""

    def __init__(self, t0: float = 0.0) -> None:
        self.t = t0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.t

    async def sleep(self, dt: float) -> None:
        self.sleeps.append(dt)
        self.t += dt


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


def _bucket(rate: float, burst: int, clock: FakeClock) -> TokenBucket:
    return TokenBucket(rate_per_sec=rate, burst=burst, time_fn=clock.time, sleep_fn=clock.sleep)


class TestTokenBucket:
    @pytest.mark.asyncio
    async def test_burst_acquires_do_not_sleep(self, clock: FakeClock) -> None:
        b = _bucket(1.0, burst=5, clock=clock)
        for _ in range(5):
            await b.acquire()
        assert clock.sleeps == []

    @pytest.mark.asyncio
    async def test_exceeding_burst_sleeps_by_one_over_rate(self, clock: FakeClock) -> None:
        b = _bucket(2.0, burst=3, clock=clock)  # 2 tokens/sec, 3 burst
        for _ in range(3):
            await b.acquire()
        assert clock.sleeps == []

        await b.acquire()  # bucket empty → must wait 1/rate = 0.5s
        assert clock.sleeps == pytest.approx([0.5])

    @pytest.mark.asyncio
    async def test_tokens_refill_over_time(self, clock: FakeClock) -> None:
        b = _bucket(2.0, burst=2, clock=clock)
        await b.acquire()
        await b.acquire()  # bucket now empty
        clock.t += 1.0  # 1s of "real time" — 2 tokens refill
        await b.acquire()
        assert clock.sleeps == []  # tokens were there, no wait needed

    @pytest.mark.asyncio
    async def test_refill_capped_at_burst(self, clock: FakeClock) -> None:
        b = _bucket(2.0, burst=3, clock=clock)
        # Drain
        for _ in range(3):
            await b.acquire()
        clock.t += 10_000  # ludicrous time elapse — should cap at 3, not 20000
        # Should be able to burst-acquire exactly 3 more without sleeping
        for _ in range(3):
            await b.acquire()
        assert clock.sleeps == []
        # Fourth one forces a wait
        await b.acquire()
        assert len(clock.sleeps) == 1

    def test_invalid_rate_rejected(self) -> None:
        with pytest.raises(ValueError):
            TokenBucket(rate_per_sec=0, burst=1)

    def test_invalid_burst_rejected(self) -> None:
        with pytest.raises(ValueError):
            TokenBucket(rate_per_sec=1, burst=0)
