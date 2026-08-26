"""Shared test fixtures.

Every test gets an isolated env + settings cache — no config leakage from
one test to the next.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from trend_radar.config import get_settings


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Clear env of any TREND_RADAR_/ANTHROPIC_/LOGFIRE_ vars,
    point db and log dir at a tmp path, and reset the settings cache."""
    for k in list(monkeypatch.__dict__.get("_setitem", [])):  # defensive
        pass
    for key in list(dict.fromkeys(k for k in list(__import__("os").environ) if k.startswith(
        ("TREND_RADAR_", "ANTHROPIC_", "LOGFIRE_")
    ))):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("TREND_RADAR_DB", str(tmp_path / "trend_radar.db"))
    monkeypatch.setenv("TREND_RADAR_LOG_DIR", str(tmp_path))
    # No .env file lookup in tests.
    monkeypatch.chdir(tmp_path)

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
