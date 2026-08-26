"""Novelty gate tests: rapidfuzz comparison + suppression thresholds."""
from __future__ import annotations

from datetime import date

import pytest

from trend_radar.config import ScoringConfig
from trend_radar.models import NormalizedTopic
from trend_radar.novelty import NoveltyGate


def _topic(title: str, one_line: str = "") -> NormalizedTopic:
    return NormalizedTopic(canonical_title=title, one_line=one_line or title, entities=[], tags=[])


def _ledger_entry(topic_id: str, title: str, one_line: str = "") -> tuple[str, str, str, date]:
    return (topic_id, title, one_line or title, date(2026, 8, 20))


class TestCompare:
    def test_empty_ledger_returns_none(self) -> None:
        gate = NoveltyGate(ScoringConfig())
        assert gate.compare(_topic("Claude Opus 5"), []) is None

    def test_exact_match_high_similarity(self) -> None:
        gate = NoveltyGate(ScoringConfig())
        ledger = [_ledger_entry("a" * 12, "Claude Opus 5 released")]
        hit = gate.compare(_topic("Claude Opus 5 released"), ledger)
        assert hit is not None
        assert hit.similarity == pytest.approx(1.0)
        assert hit.canonical_title == "Claude Opus 5 released"

    def test_unrelated_titles_low_similarity(self) -> None:
        gate = NoveltyGate(ScoringConfig())
        ledger = [_ledger_entry("a" * 12, "TensorRT-LLM speculative decoding")]
        hit = gate.compare(_topic("Claude Opus 5 released"), ledger)
        assert hit is not None
        # token_set_ratio for these unrelated titles sits around 0.33.
        # The point of this test is "well below the 0.88 suppression threshold".
        assert hit.similarity < 0.5

    def test_picks_highest_similarity_among_multiple(self) -> None:
        gate = NoveltyGate(ScoringConfig())
        ledger = [
            _ledger_entry("a" * 12, "Random unrelated post"),
            _ledger_entry("b" * 12, "Claude Opus 5 launches"),
            _ledger_entry("c" * 12, "Also unrelated"),
        ]
        hit = gate.compare(_topic("Claude Opus 5 released"), ledger)
        assert hit is not None
        assert hit.topic_id == "b" * 12

    def test_embed_model_raises_not_implemented(self) -> None:
        gate = NoveltyGate(ScoringConfig(), embed_model="text-embedding-3-small")
        with pytest.raises(NotImplementedError, match="TREND_RADAR_EMBED_MODEL"):
            gate.compare(_topic("anything"), [_ledger_entry("a" * 12, "x")])


class TestMultiplierAndSuppression:
    def test_at_threshold_is_suppressed(self) -> None:
        gate = NoveltyGate(ScoringConfig(novelty_fuzz_threshold=88))
        mult, suppressed, reason = gate.multiplier(0.88)
        assert suppressed is True
        assert mult == 0.0
        assert reason is not None and "similarity" in reason

    def test_above_threshold_is_suppressed(self) -> None:
        gate = NoveltyGate(ScoringConfig(novelty_fuzz_threshold=88))
        mult, suppressed, _ = gate.multiplier(0.95)
        assert suppressed is True
        assert mult == 0.0

    def test_zero_similarity_is_fully_novel(self) -> None:
        gate = NoveltyGate(ScoringConfig(novelty_fuzz_threshold=88))
        mult, suppressed, reason = gate.multiplier(0.0)
        assert suppressed is False
        assert mult == 1.0
        assert reason is None

    def test_within_safe_zone_is_fully_novel(self) -> None:
        # safe zone = 0.5 * threshold = 0.44
        gate = NoveltyGate(ScoringConfig(novelty_fuzz_threshold=88))
        mult, suppressed, _ = gate.multiplier(0.30)
        assert suppressed is False
        assert mult == 1.0

    def test_between_safe_zone_and_threshold_decays_linearly(self) -> None:
        # safe_end = 0.44, threshold = 0.88 → midpoint = 0.66 → mult = 0.5
        gate = NoveltyGate(ScoringConfig(novelty_fuzz_threshold=88))
        mult, suppressed, _ = gate.multiplier(0.66)
        assert suppressed is False
        assert mult == pytest.approx(0.5, abs=0.01)

    def test_multiplier_monotonic_decreasing(self) -> None:
        gate = NoveltyGate(ScoringConfig(novelty_fuzz_threshold=88))
        similarities = [0.0, 0.3, 0.5, 0.6, 0.7, 0.85]
        mults = [gate.multiplier(s)[0] for s in similarities]
        for a, b in zip(mults, mults[1:]):
            assert a >= b, f"multiplier not monotonic: {mults}"


class TestCheck:
    def test_novel_when_ledger_empty(self) -> None:
        gate = NoveltyGate(ScoringConfig())
        r = gate.check(_topic("Anything"), [])
        assert r.is_novel is True
        assert r.max_similarity == 0.0
        assert r.closest_match_title is None

    def test_suppressed_when_similar(self) -> None:
        gate = NoveltyGate(ScoringConfig(novelty_fuzz_threshold=88))
        ledger = [_ledger_entry("a" * 12, "Claude Opus 5 released", "Anthropic ships Opus 5")]
        r = gate.check(_topic("Claude Opus 5 released", "Anthropic ships Opus 5"), ledger)
        assert r.is_novel is False
        assert r.max_similarity >= 0.88
        assert r.closest_match_title == "Claude Opus 5 released"
        assert r.closest_match_date == date(2026, 8, 20)

    def test_novel_when_below_threshold(self) -> None:
        gate = NoveltyGate(ScoringConfig(novelty_fuzz_threshold=88))
        ledger = [_ledger_entry("a" * 12, "TensorRT-LLM release")]
        r = gate.check(_topic("Claude Opus 5 released"), ledger)
        assert r.is_novel is True
        assert r.closest_match_title == "TensorRT-LLM release"
