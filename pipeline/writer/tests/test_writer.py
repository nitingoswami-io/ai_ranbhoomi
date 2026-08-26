"""Scaffold tests — models parse, dry-run wires end-to-end, prompt renders.

No LLM calls: the writer's real behaviour needs live model access and
belongs behind a marker (add `@pytest.mark.live` and a network gate when
you flesh this out).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from writer.agent import (
    MAX_WORDS,
    MIN_WORDS,
    _word_count_error,
    render_topic_prompt,
)
from writer.cli import main
from writer.models import DraftBundle, TrendingResultIn

FIXTURE = Path(__file__).parent / "fixtures" / "trending_sample.json"


def _load_fixture() -> TrendingResultIn:
    return TrendingResultIn.model_validate_json(FIXTURE.read_text())


def test_fixture_parses_as_trending_result():
    trending = _load_fixture()
    assert trending.run_id == "test-run-0001"
    assert len(trending.topics) == 2
    assert trending.topics[0].suppressed is False
    assert trending.topics[1].suppressed is True


def test_prompt_includes_grounding_numbers():
    trending = _load_fixture()
    prompt = render_topic_prompt(trending.topics[0])
    assert "GPT-6" in prompt
    assert "428" in prompt              # raw_score of top item
    assert "final_score" in prompt
    assert "corroboration_bonus" in prompt
    assert "Ranked #1" in prompt        # from score.explanation
    for entity in trending.topics[0].topic.entities:
        assert entity in prompt


def test_dry_run_emits_valid_bundle(tmp_path, capsys):
    out = tmp_path / "drafts.json"
    rc = main([
        "--input", str(FIXTURE),
        "--output", str(out),
        "--dry-run",
    ])
    assert rc == 0

    bundle = DraftBundle.model_validate_json(out.read_text())
    assert bundle.run_id == "test-run-0001"
    assert len(bundle.drafts) == 1  # suppressed topic excluded by default
    draft = bundle.drafts[0]
    assert draft.topic_id == "a1b2c3d4e5f6"
    assert draft.ranking_rationale.startswith("Ranked #1")
    assert draft.sources, "draft must carry at least one source"


def test_dry_run_include_suppressed(tmp_path):
    out = tmp_path / "drafts.json"
    rc = main([
        "--input", str(FIXTURE),
        "--output", str(out),
        "--dry-run",
        "--include-suppressed",
    ])
    assert rc == 0
    bundle = DraftBundle.model_validate_json(out.read_text())
    assert len(bundle.drafts) == 2


def test_empty_topics_yields_empty_bundle(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({
        "run_id": "empty",
        "generated_at": "2026-08-25T09:00:00Z",
        "lookback_hours": 24,
        "clustering_method": "lexical",
        "source_health": [],
        "topics": [],
    }))
    out = tmp_path / "drafts.json"
    rc = main(["--input", str(empty), "--output", str(out), "--dry-run"])
    assert rc == 0
    bundle = DraftBundle.model_validate_json(out.read_text())
    assert bundle.drafts == []


# --- word-count enforcement ---------------------------------------------


def test_word_count_in_range_returns_none():
    body = " ".join(["word"] * ((MIN_WORDS + MAX_WORDS) // 2))
    assert _word_count_error(body) is None


def test_word_count_at_boundaries_is_ok():
    assert _word_count_error(" ".join(["w"] * MIN_WORDS)) is None
    assert _word_count_error(" ".join(["w"] * MAX_WORDS)) is None


def test_word_count_too_short_flags_and_names_gap():
    body = " ".join(["short"] * (MIN_WORDS - 50))
    err = _word_count_error(body)
    assert err is not None
    assert str(MIN_WORDS) in err
    assert "50" in err  # names the gap


def test_word_count_too_long_flags_and_names_overshoot():
    body = " ".join(["long"] * (MAX_WORDS + 30))
    err = _word_count_error(body)
    assert err is not None
    assert str(MAX_WORDS) in err
    assert "30" in err  # names the overshoot


def test_thin_coverage_escape_bypasses_word_count():
    body = "Thin coverage — not enough signal to draft. Consider skipping."
    assert _word_count_error(body) is None


def test_leading_whitespace_does_not_defeat_thin_coverage():
    body = "   \n  Thin coverage — not enough signal to draft. Consider skipping."
    assert _word_count_error(body) is None


@pytest.mark.skip(reason="requires ANTHROPIC_API_KEY and network — enable when you flesh out the agent")
def test_write_draft_live():
    """Placeholder for a real LLM run. Mark with @pytest.mark.live once wired."""
