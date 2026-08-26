"""Error envelope + tool_boundary decorator."""
from __future__ import annotations

import pytest

from trend_radar.errors import ToolError, format_tool_error, redact, tool_boundary


class TestFormat:
    def test_format_has_expected_shape(self) -> None:
        msg = format_tool_error("not_found", "no runs yet", "call get_trending_topics")
        assert msg == "[not_found] no runs yet\nNext step: call get_trending_topics"

    def test_tool_error_model_validates_codes(self) -> None:
        # Only whitelisted codes accepted
        with pytest.raises(Exception):
            ToolError(code="wat", message="x", next_step="y")  # type: ignore[arg-type]


class TestRedact:
    def test_bearer_token_redacted(self) -> None:
        assert "***REDACTED***" in redact("Authorization: Bearer sk-abc123def456ghi789jkl")

    def test_apikey_kv_redacted(self) -> None:
        assert "***REDACTED***" in redact("api_key=abc123def456ghi")

    def test_plain_text_untouched(self) -> None:
        assert redact("nothing sensitive here") == "nothing sensitive here"


class TestToolBoundary:
    @pytest.mark.asyncio
    async def test_value_error_passes_through_unchanged(self) -> None:
        @tool_boundary
        async def fn() -> str:
            raise ValueError("[not_found] original message")
        with pytest.raises(ValueError, match=r"^\[not_found\] original message$"):
            await fn()

    @pytest.mark.asyncio
    async def test_unexpected_exception_wrapped_as_internal(self) -> None:
        @tool_boundary
        async def fn() -> str:
            raise RuntimeError("kaboom")
        with pytest.raises(ValueError) as exc_info:
            await fn()
        msg = str(exc_info.value)
        assert msg.startswith("[internal]")
        assert "RuntimeError" in msg
        assert "kaboom" in msg
        assert "Next step:" in msg

    @pytest.mark.asyncio
    async def test_credentials_redacted_from_wrapped_error(self) -> None:
        @tool_boundary
        async def fn() -> str:
            raise RuntimeError("failed with Authorization: Bearer sk-abcdef1234567890")
        with pytest.raises(ValueError) as exc_info:
            await fn()
        assert "sk-abcdef1234567890" not in str(exc_info.value)
        assert "REDACTED" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_happy_path_returns_value(self) -> None:
        @tool_boundary
        async def fn(x: int) -> int:
            return x * 2
        assert await fn(21) == 42

    def test_signature_preserved_via_functools_wraps(self) -> None:
        import inspect

        @tool_boundary
        async def fn(x: int, y: str = "z") -> bool:  # noqa: ANN201
            return True

        sig = inspect.signature(fn)
        assert list(sig.parameters) == ["x", "y"]
        # `from __future__ import annotations` makes hints strings — compare by name.
        assert sig.parameters["x"].annotation in (int, "int")
        assert sig.parameters["y"].default == "z"
