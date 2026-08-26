"""Observability.

Stdio transport rule: stdout is the JSON-RPC channel. Nothing but MCP
messages goes there — ever. Logs go to stderr and to a rotating file in
`settings.log_dir`. Logfire is optional: if `LOGFIRE_TOKEN` is set and the
`logfire` package is installed, we hook it up; otherwise silent no-op.
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from trend_radar.config import AppSettings
from trend_radar.errors import redact

_INITIALIZED = False


class _RedactingFormatter(logging.Formatter):
    """Formatter that runs every message through the secret redactor."""

    def format(self, record: logging.LogRecord) -> str:
        s = super().format(record)
        return redact(s)


def setup_logging(settings: AppSettings) -> logging.Logger:
    """Configure the root trend_radar logger. Idempotent."""
    global _INITIALIZED
    logger = logging.getLogger("trend_radar")
    if _INITIALIZED:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False

    fmt = _RedactingFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # stderr — mandatory per stdio spec.
    stderr_h = logging.StreamHandler(stream=sys.stderr)
    stderr_h.setFormatter(fmt)
    logger.addHandler(stderr_h)

    # File sink alongside the DB. Best-effort — if the directory isn't
    # writable (e.g. bare `python -m trend_radar` on macOS with the /data
    # default), log a one-line info and continue with stderr only.
    log_dir = settings.log_dir or settings.db_path.parent
    try:
        log_path = _ensure_log_path(log_dir)
        file_h = RotatingFileHandler(log_path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        file_h.setFormatter(fmt)
        logger.addHandler(file_h)
    except OSError as exc:
        logger.info(
            "file log sink disabled (%s not writable: %s); using stderr only. "
            "Override TREND_RADAR_DB or TREND_RADAR_LOG_DIR to point at a writable path.",
            log_dir, exc.strerror or exc,
        )

    # Optional logfire.
    _try_init_logfire(settings, logger)

    _INITIALIZED = True
    return logger


def _ensure_log_path(log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "trend_radar.log"


def _try_init_logfire(settings: AppSettings, logger: logging.Logger) -> None:
    if settings.logfire_token is None:
        return
    try:
        import logfire  # type: ignore

        logfire.configure(token=settings.logfire_token.get_secret_value(), send_to_logfire=True)
        logger.info("logfire enabled")
    except ImportError:
        logger.info("LOGFIRE_TOKEN set but logfire package not installed; skipping")
    except Exception as exc:  # noqa: BLE001 — never let telemetry init break the server
        logger.warning("logfire init failed: %s", exc)


def get_logger(name: str | None = None) -> logging.Logger:
    """Get a child logger under the trend_radar namespace."""
    if name:
        return logging.getLogger(f"trend_radar.{name}")
    return logging.getLogger("trend_radar")
