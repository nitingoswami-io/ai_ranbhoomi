"""Apple Notes MCP server.

Runs in a Docker container under the Docker MCP Toolkit. All Notes work
happens on the macOS host — this container forwards each tool call to the
notes-bridge daemon over HTTP via host.docker.internal.

Env vars:
    NOTES_BRIDGE_URL     default http://host.docker.internal:48213
    NOTES_BRIDGE_SECRET  shared secret (must match the bridge's)

SDK: mcp >= 2.0 — see the sibling dice server for the same pattern.
STDIO RULE: never print to stdout. Diagnostics go to stderr; the gateway
reads stdout as the JSON-RPC transport.
"""

import json
import os
import sys
import urllib.error
import urllib.request

from mcp.server.mcpserver import MCPServer

BRIDGE_URL = os.environ.get("NOTES_BRIDGE_URL", "http://host.docker.internal:48213").rstrip("/")
SECRET = os.environ.get("NOTES_BRIDGE_SECRET", "")
TIMEOUT = int(os.environ.get("NOTES_BRIDGE_TIMEOUT", "60"))

mcp = MCPServer(name="apple-notes", version="0.1.0")


def _call(action: str, params: dict | None = None):
    body = json.dumps({"action": action, "params": params or {}}).encode("utf-8")
    req = urllib.request.Request(
        f"{BRIDGE_URL}/call",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Bridge-Secret": SECRET,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        # Bridge returned an error status — the body still carries JSON.
        try:
            payload = json.loads(e.read())
        except Exception:
            raise RuntimeError(f"bridge HTTP {e.code}: {e.reason}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"cannot reach notes-bridge at {BRIDGE_URL} — is it running on the host? ({e.reason})"
        ) from None

    if not payload.get("ok"):
        raise RuntimeError(payload.get("error") or "unknown bridge error")

    print(f"[notes-mcp] {action} ok", file=sys.stderr)
    return payload.get("data")


@mcp.tool()
def list_notes(folder: str | None = None, limit: int | None = 50) -> list:
    """List notes in Apple Notes as {id, name, folder, modified, created}.

    Args:
        folder: If set, only notes inside this folder name.
        limit: Maximum notes to return (default 50, set None for all — slow on large libraries).
    """
    return _call("list_notes", {"folder": folder, "limit": limit})


@mcp.tool()
def get_note(id: str) -> dict:
    """Fetch the full body of a note by its id (from list_notes / search_notes).

    Returns both `body` (HTML as stored by Notes) and `plaintext`.
    """
    return _call("get_note", {"id": id})


@mcp.tool()
def create_note(name: str, body: str = "", folder: str | None = None) -> dict:
    """Create a new note.

    Args:
        name: Note title (shows as the first line in the Notes UI too).
        body: Note body. Accepts simple HTML — Notes stores everything as HTML.
              A plain string will render as a single paragraph.
        folder: Target folder name. Omit to create in the default folder.
    """
    return _call("create_note", {"name": name, "body": body, "folder": folder})


@mcp.tool()
def update_note(
    id: str,
    name: str | None = None,
    body: str | None = None,
    mode: str = "replace",
) -> dict:
    """Update a note's title and/or body.

    Args:
        id: Note id.
        name: New title. Omit to leave unchanged.
        body: New body content. Omit to leave unchanged.
        mode: "replace" (default) overwrites the body; "append" adds to the end.
    """
    if mode not in ("replace", "append"):
        raise ValueError("mode must be 'replace' or 'append'")
    return _call(
        "update_note",
        {"id": id, "name": name, "body": body, "mode": mode},
    )


@mcp.tool()
def delete_note(id: str) -> dict:
    """Move a note to Recently Deleted. Not a hard delete — recoverable for 30 days."""
    return _call("delete_note", {"id": id})


@mcp.tool()
def list_folders() -> list:
    """List all folders in Apple Notes as {id, name}."""
    return _call("list_folders")


@mcp.tool()
def search_notes(query: str, limit: int = 25) -> list:
    """Search notes whose title or plaintext body contains the query (case-insensitive).

    Returns note summaries (no body). Use get_note to fetch the full body.
    """
    return _call("search_notes", {"query": query, "limit": limit})


if __name__ == "__main__":
    mcp.run(transport="stdio")
