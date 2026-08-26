"""Error envelope used at the MCP tool boundary.

Every failure that reaches a tool must be turned into a `ToolError` with a
concrete `next_step` string. The MCP client should be able to act on the
message alone — no stack traces in the response.

Usage from a tool body:
    raise ValueError(format_tool_error("not_found", "topic_id not in last run", "call get_trending_topics"))

Or wrap the whole body with @tool_boundary — unexpected exceptions become
`[internal] ...` errors with the exception type and a redacted message.
"""
from __future__ import annotations

import functools
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field

ErrorCode = Literal[
    "config_missing",
    "credentials_invalid",
    "upstream_unavailable",
    "rate_limited",
    "not_found",
    "invalid_argument",
    "storage_unavailable",
    "internal",
]


class ToolError(BaseModel):
    """Structured error surface. `format_tool_error` produces the human string."""

    code: ErrorCode
    message: str = Field(..., description="One line, human-readable.")
    next_step: str = Field(..., description="Concrete action the user can take right now.")


def format_tool_error(code: ErrorCode, message: str, next_step: str) -> str:
    """Format a structured error as a single string for `raise ValueError(...)`.

    Format is stable so clients can pattern-match if needed:
        [<code>] <message>
        Next step: <next_step>
    """
    return f"[{code}] {message}\nNext step: {next_step}"


_TOOL_LOG = logging.getLogger("trend_radar.tools")
F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def tool_boundary(fn: F) -> F:
    """Wrap an async tool body so unexpected exceptions become structured errors.

    Preserves signature via functools.wraps — the MCP SDK's parameter
    introspection follows __wrapped__ and still sees the original type hints.
    ValueErrors raised by the body pass through untouched (they're the tool's
    own errors, already formatted).
    """
    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except ValueError:
            raise
        except Exception as exc:
            _TOOL_LOG.exception("unexpected error in tool %s", fn.__name__)
            raise ValueError(
                format_tool_error(
                    "internal",
                    f"{type(exc).__name__}: {redact(str(exc))}",
                    "check server stderr/log for the traceback; run get_source_config to sanity-check config",
                )
            ) from exc

    return wrapper  # type: ignore[return-value]


# --- Secret redaction for log lines ----------------------------------------

_SECRET_PATTERNS: list[re.Pattern[str]] = [
    # Bearer tokens
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._\-]{16,})"),
    # generic key=value where key ends in secret/token/key/password
    re.compile(r"(?i)((?:api[_-]?key|secret|token|password)\s*[:=]\s*)([^\s,]{6,})"),
    # sk-anthropic style
    re.compile(r"\bsk-[A-Za-z0-9\-_]{16,}\b"),
]


def redact(text: str) -> str:
    """Redact anything that looks like a credential from a log string.

    Precision-favoring: over-redacts on ambiguity. Log lines are diagnostic,
    not evidence — a masked hint is better than a leaked key.
    """
    out = text
    for pat in _SECRET_PATTERNS:
        out = pat.sub(lambda m: (m.group(1) + "***REDACTED***") if m.lastindex else "***REDACTED***", out)
    return out
