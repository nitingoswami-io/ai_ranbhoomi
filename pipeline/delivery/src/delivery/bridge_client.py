"""HTTP client for the apple-notes bridge.

Talks the same wire protocol as the MCP server itself: POST /call with
{action, params}, X-Bridge-Secret header for auth, {ok, data|error}
JSON responses.

Kept thin — no retries, no connection pooling. The bridge is on
localhost; if it can't be reached, the right thing is to fail loud and
let the pipeline caller decide (usually: check the LaunchAgent).
"""
from __future__ import annotations

import httpx

DEFAULT_URL = "http://localhost:48213"
DEFAULT_TIMEOUT = 60.0


class BridgeError(RuntimeError):
    """Raised for any non-2xx response or transport failure."""


class BridgeClient:
    def __init__(
        self,
        url: str = DEFAULT_URL,
        secret: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.url = url.rstrip("/")
        self.secret = secret
        self._client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "BridgeClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.secret:
            h["X-Bridge-Secret"] = self.secret
        return h

    def health(self) -> dict:
        """GET /health. Returns the JSON body verbatim — {ok, notes: reachable|not-reachable}."""
        try:
            r = self._client.get(f"{self.url}/health")
        except httpx.HTTPError as e:
            raise BridgeError(f"bridge unreachable at {self.url}: {e}") from e
        return r.json()

    def create_note(self, name: str, body_html: str, folder: str | None = None) -> dict:
        """Create a note. Returns the bridge's `data` dict — {id, name, folder, ...}."""
        payload = {
            "action": "create_note",
            "params": {"name": name, "body": body_html, "folder": folder},
        }
        try:
            r = self._client.post(f"{self.url}/call", json=payload, headers=self._headers())
        except httpx.HTTPError as e:
            raise BridgeError(f"bridge POST failed: {e}") from e

        try:
            body = r.json()
        except ValueError as e:
            raise BridgeError(f"bridge returned non-JSON (status {r.status_code}): {r.text[:200]!r}") from e

        if r.status_code == 401 or body.get("error") == "unauthorized":
            raise BridgeError("bridge rejected the request (401 unauthorized) — check NOTES_BRIDGE_SECRET")
        if not body.get("ok"):
            raise BridgeError(f"bridge action failed: {body.get('error', 'unknown error')}")

        data = body.get("data")
        if not isinstance(data, dict) or "id" not in data:
            raise BridgeError(f"bridge returned ok but no data.id: {body!r}")
        return data
