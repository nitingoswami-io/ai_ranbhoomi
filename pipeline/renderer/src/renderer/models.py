"""Data contract for the renderer stage.

Input: a subset mirror of `writer.models.DraftBundle`. Source of truth is
`pipeline/writer/src/writer/models.py`; we only re-declare the fields we
read, and set `extra='ignore'` so writer can grow fields without breaking
the renderer.

Output: one of three target-specific bundles — `MarkdownBundle`,
`AppleNotesBundle`, or `NotionBundle`. Each piece type is target-shaped
so downstream delivery code doesn't need to unwrap a union.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

_lax = ConfigDict(extra="ignore")

RenderTarget = Literal["markdown", "apple-notes", "notion"]
SourceName = Literal["hackernews", "arxiv", "huggingface"]


# --- Input: subset of writer's DraftBundle -------------------------------

class DraftSourceIn(BaseModel):
    model_config = _lax
    title: str
    url: HttpUrl
    source: SourceName
    signal: str


class DraftIn(BaseModel):
    model_config = _lax
    topic_id: str
    canonical_title: str
    headline: str
    subhead: str
    body_md: str
    key_signals: list[str] = Field(default_factory=list)
    sources: list[DraftSourceIn] = Field(default_factory=list)
    ranking_rationale: str


class DraftBundleIn(BaseModel):
    model_config = _lax
    run_id: str
    generated_at: datetime
    drafts: list[DraftIn]


# --- Output: one piece type per target ------------------------------------

class MarkdownPiece(BaseModel):
    """Full-post markdown, ready for review, archival, or feeding another tool."""

    topic_id: str
    title: str
    body: str = Field(..., description="Full markdown, including headings, signals, sources.")


class AppleNotesPiece(BaseModel):
    """Ready-to-send payload for `apple-notes.create_note(name, body, folder)`.

    `body_html` is the actual body — the apple-notes MCP tool stores
    everything as HTML, so we convert markdown up front here.
    """

    topic_id: str
    name: str = Field(..., description="Note title. Passed as `name` to create_note.")
    body_html: str = Field(..., description="Note body as HTML. Passed as `body`.")
    folder: str | None = Field(default=None, description="Passed as `folder` to create_note.")


class NotionPiece(BaseModel):
    """Ready-to-send payload for the Notion API / MCP `notion-create-pages` shape.

    `blocks` is a list of Notion block objects. Downstream delivery wraps
    them in a page with the given `title` under a chosen parent.
    """

    topic_id: str
    title: str
    blocks: list[dict[str, Any]] = Field(
        ...,
        description="Notion block objects — paragraphs, headings, bulleted_list_item, etc.",
    )


class MarkdownBundle(BaseModel):
    run_id: str
    target: Literal["markdown"] = "markdown"
    pieces: list[MarkdownPiece]


class AppleNotesBundle(BaseModel):
    run_id: str
    target: Literal["apple-notes"] = "apple-notes"
    pieces: list[AppleNotesPiece]


class NotionBundle(BaseModel):
    run_id: str
    target: Literal["notion"] = "notion"
    pieces: list[NotionPiece]
