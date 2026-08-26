"""Markdown → Notion blocks for the notion target.

Emits the Notion API block shapes documented at
https://developers.notion.com/reference/block. Covers what a Draft
actually produces: heading_1/2/3, paragraph, bulleted_list_item, divider.
Inline styling (bold, italic, links) is emitted as `rich_text` spans with
per-span `annotations`.

If the writer's output grows to need code blocks, tables, or nested
lists, extend `_line_to_block` and `_split_inline` — the function shapes
are stable.
"""
from __future__ import annotations

import re
from typing import Any

from renderer.markdown import render_markdown
from renderer.models import DraftIn, NotionPiece

_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")


def _text_span(content: str, *, bold: bool = False, italic: bool = False, url: str | None = None) -> dict[str, Any]:
    span: dict[str, Any] = {
        "type": "text",
        "text": {"content": content, "link": {"url": url} if url else None},
        "annotations": {
            "bold": bold, "italic": italic, "strikethrough": False,
            "underline": False, "code": False, "color": "default",
        },
        "plain_text": content,
        "href": url,
    }
    return span


def _split_inline(text: str) -> list[dict[str, Any]]:
    """Turn one line of markdown into Notion `rich_text` spans.

    Handles links, bold, italic. Overlapping/nested inline styles get
    flattened — links dominate, then bold, then italic. Good enough for
    the writer's prose; extend if needed.
    """
    spans: list[dict[str, Any]] = []
    pos = 0
    # First split on links so URLs are preserved as separate spans.
    for m in _LINK_RE.finditer(text):
        if m.start() > pos:
            spans.extend(_style_spans(text[pos : m.start()]))
        spans.append(_text_span(m.group(1), url=m.group(2)))
        pos = m.end()
    if pos < len(text):
        spans.extend(_style_spans(text[pos:]))
    return spans or [_text_span("")]


def _style_spans(text: str) -> list[dict[str, Any]]:
    """Split a link-free string into bold/italic/plain spans."""
    spans: list[dict[str, Any]] = []
    pos = 0
    # Bold first (double-star is unambiguous), then italic on what's left.
    for m in _BOLD_RE.finditer(text):
        if m.start() > pos:
            spans.extend(_italic_or_plain(text[pos : m.start()]))
        spans.append(_text_span(m.group(1), bold=True))
        pos = m.end()
    if pos < len(text):
        spans.extend(_italic_or_plain(text[pos:]))
    return spans


def _italic_or_plain(text: str) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    pos = 0
    for m in _ITALIC_RE.finditer(text):
        if m.start() > pos:
            spans.append(_text_span(text[pos : m.start()]))
        spans.append(_text_span(m.group(1), italic=True))
        pos = m.end()
    if pos < len(text):
        spans.append(_text_span(text[pos:]))
    return spans or [_text_span(text)]


def _block(block_type: str, rich_text: list[dict[str, Any]]) -> dict[str, Any]:
    return {"object": "block", "type": block_type, block_type: {"rich_text": rich_text}}


def markdown_to_blocks(md: str) -> list[dict[str, Any]]:
    """Convert the markdown subset produced by render_markdown to Notion blocks."""
    blocks: list[dict[str, Any]] = []
    paragraph: list[str] = []

    def _flush_paragraph() -> None:
        if paragraph:
            joined = " ".join(paragraph)
            blocks.append(_block("paragraph", _split_inline(joined)))
            paragraph.clear()

    for raw in md.splitlines():
        line = raw.rstrip()

        if not line:
            _flush_paragraph()
            continue

        if line.startswith("### "):
            _flush_paragraph()
            blocks.append(_block("heading_3", _split_inline(line[4:])))
        elif line.startswith("## "):
            _flush_paragraph()
            blocks.append(_block("heading_2", _split_inline(line[3:])))
        elif line.startswith("# "):
            _flush_paragraph()
            blocks.append(_block("heading_1", _split_inline(line[2:])))
        elif line.strip() == "---":
            _flush_paragraph()
            blocks.append({"object": "block", "type": "divider", "divider": {}})
        elif line.startswith("- "):
            _flush_paragraph()
            blocks.append(_block("bulleted_list_item", _split_inline(line[2:])))
        else:
            paragraph.append(line)

    _flush_paragraph()
    return blocks


def render_notion(draft: DraftIn) -> NotionPiece:
    md = render_markdown(draft)
    return NotionPiece(topic_id=draft.topic_id, title=draft.headline, blocks=markdown_to_blocks(md))
