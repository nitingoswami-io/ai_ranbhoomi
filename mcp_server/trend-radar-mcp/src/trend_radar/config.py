"""Runtime configuration.

All settings load from environment variables (via .env in dev, Docker MCP
secrets in prod). Nested delimiter is `__` per pydantic-settings — so
scoring constants are overridden like:

    TREND_RADAR_SCORING__CORROBORATION_PER_SOURCE=0.20
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ScoringConfig(BaseModel):
    """Deterministic scoring constants. Every ranking decision derives from these."""

    velocity_age_offset_hours: float = Field(
        2.0,
        gt=0,
        description="Gravity offset in the velocity formula: raw_score / (age_h + offset)^exp.",
    )
    velocity_exponent: float = Field(1.5, gt=0)

    corroboration_per_source: float = Field(0.15, ge=0)
    # 3 sources max (hackernews, arxiv, huggingface) → max bonus (3-1)*0.15 = 0.30.
    # Cap matches the ceiling for honesty; raise per_source above 0.15 to feel the cap.
    corroboration_cap: float = Field(0.30, ge=0)

    baseline_source_percentile: float = Field(
        0.5,
        ge=0.0,
        le=1.0,
        description="Fixed percentile for arXiv, which has no comparable score signal (HN uses points, HF uses upvotes).",
    )

    # Novelty gate — used by the ledger, kept here so it's overridable + visible via get_source_config.
    novelty_embedding_threshold: float = Field(0.85, ge=0.0, le=1.0)
    novelty_fuzz_threshold: int = Field(88, ge=0, le=100)
    novelty_lookback_days: int = Field(90, gt=0)


class AppSettings(BaseSettings):
    """Top-level settings loaded from env + .env file."""

    model_config = SettingsConfigDict(
        env_prefix="TREND_RADAR_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Storage & logs -------------------------------------------------
    # Accepts TREND_RADAR_DB (spec) OR TREND_RADAR_DB_PATH (pydantic default).
    db_path: Path = Field(
        default=Path("/data/trend_radar.db"),
        validation_alias=AliasChoices("TREND_RADAR_DB", "TREND_RADAR_DB_PATH"),
    )
    # If unset, derives from db_path.parent — override one env var and both
    # move together. Explicit override still wins.
    log_dir: Path | None = None

    # --- Clustering -----------------------------------------------------
    llm_model: str = "anthropic:claude-sonnet-4-6"
    anthropic_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY")
    )

    # --- Novelty --------------------------------------------------------
    embed_model: str | None = None

    # --- Runtime knobs --------------------------------------------------
    max_items_per_topic: int = Field(5, gt=0, le=50)

    # --- Observability --------------------------------------------------
    logfire_token: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("LOGFIRE_TOKEN")
    )

    # --- Nested scoring config -----------------------------------------
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)

    @model_validator(mode="after")
    def _default_log_dir(self) -> AppSettings:
        """Colocate logs next to the DB unless the user pinned log_dir explicitly."""
        if self.log_dir is None:
            self.log_dir = self.db_path.parent
        return self

    def has_anthropic_key(self) -> bool:
        return self.anthropic_api_key is not None


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Cached settings singleton. Tests can call `get_settings.cache_clear()`."""
    return AppSettings()
