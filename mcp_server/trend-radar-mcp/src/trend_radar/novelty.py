"""Novelty gate: rapidfuzz similarity against the covered_topics ledger.

Stateless — the caller loads the ledger once per run and passes it in.

Embedding-based similarity (§5) is not implemented in phase 3. Setting
TREND_RADAR_EMBED_MODEL raises `NotImplementedError` rather than silently
downgrading, so misconfig is loud.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from rapidfuzz import fuzz

from trend_radar.config import ScoringConfig
from trend_radar.models import NormalizedTopic, NoveltyResult

# Below this fraction of the fuzz threshold, the novelty multiplier is 1.0
# ("clearly novel" region per spec §5). Between here and the threshold it
# decays linearly to 0. At/above threshold: suppressed.
_SAFE_ZONE_RATIO = 0.5


@dataclass(frozen=True, slots=True)
class LedgerHit:
    topic_id: str
    canonical_title: str
    one_line: str
    covered_on: date
    similarity: float          # 0..1


# Ledger row shape produced by Storage.recent_covered_records.
LedgerRow = tuple[str, str, str, date]  # (topic_id, canonical_title, one_line, covered_on)


class NoveltyGate:
    def __init__(self, config: ScoringConfig, *, embed_model: str | None = None) -> None:
        self.config = config
        self.embed_model = embed_model

    def compare(self, topic: NormalizedTopic, ledger: list[LedgerRow]) -> LedgerHit | None:
        """Highest-similarity ledger entry against `topic`, or None if ledger empty."""
        if self.embed_model:
            raise NotImplementedError(
                "Embedding-based novelty check is not implemented. "
                "Unset TREND_RADAR_EMBED_MODEL to use rapidfuzz."
            )
        if not ledger:
            return None
        query = _norm(topic.canonical_title, topic.one_line)
        best: LedgerHit | None = None
        for topic_id, ct, one_line, covered_on in ledger:
            sim = fuzz.token_set_ratio(query, _norm(ct, one_line)) / 100.0
            if best is None or sim > best.similarity:
                best = LedgerHit(topic_id, ct, one_line, covered_on, sim)
        return best

    def multiplier(self, similarity: float) -> tuple[float, bool, str | None]:
        """Return (multiplier, suppressed, reason).

        - similarity >= threshold  -> (0.0, True, "similar to ledger entry at Nx")
        - similarity <= safe_zone  -> (1.0, False, None)
        - between                  -> linear decay, not suppressed
        """
        threshold = self.config.novelty_fuzz_threshold / 100.0
        if similarity >= threshold:
            return 0.0, True, (
                f"similarity {similarity:.2f} >= threshold "
                f"{threshold:.2f} against a topic covered in the last "
                f"{self.config.novelty_lookback_days} days"
            )
        safe_end = _SAFE_ZONE_RATIO * threshold
        if similarity <= safe_end:
            return 1.0, False, None
        span = threshold - safe_end
        mult = 1.0 - (similarity - safe_end) / span
        return mult, False, None

    def check(self, topic: NormalizedTopic, ledger: list[LedgerRow]) -> NoveltyResult:
        """Ad-hoc novelty result for the check_novelty tool."""
        hit = self.compare(topic, ledger)
        if hit is None:
            return NoveltyResult(is_novel=True, max_similarity=0.0)
        _, suppressed, _ = self.multiplier(hit.similarity)
        return NoveltyResult(
            is_novel=not suppressed,
            max_similarity=hit.similarity,
            closest_match_title=hit.canonical_title,
            closest_match_date=hit.covered_on,
        )


def _norm(*parts: str) -> str:
    """Lowercase-join for fuzz matching. token_set_ratio ignores order & dupes anyway."""
    return " ".join(p.lower().strip() for p in parts if p)
