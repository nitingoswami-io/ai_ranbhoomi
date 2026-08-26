"""Markdown → HTML for the apple-notes target.

Hand-rolled subset covering what a Draft actually produces: headings,
paragraphs, bullet lists, links, bold, italic, horizontal rule. If the
writer's output grows to need tables, code blocks, or nested lists, swap
this for `markdown-it-py` — the module boundary and function signature
are already the right shape.
"""
from __future__ import annotations

import html
import re

from renderer.markdown import render_markdown
from renderer.models import AppleNotesPiece, DraftIn

_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")


def _inline(text: str) -> str:
    """Escape then apply inline markdown → HTML on plain-text spans.

    Order matters: escape first (so user text can't inject HTML), then
    apply markdown patterns — the patterns themselves emit safe tags.
    """
    out = html.escape(text)
    out = _LINK_RE.sub(
        lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>',
        out,
    )
    out = _BOLD_RE.sub(r"<strong>\1</strong>", out)
    out = _ITALIC_RE.sub(r"<em>\1</em>", out)
    return out


def markdown_to_html(md: str) -> str:
    """Convert the markdown subset produced by render_markdown to HTML.

    Line-based parser. Blocks: h1/h2/h3, blank-separated paragraphs,
    `-` bullet lists, `---` horizontal rule.
    """
    lines = md.splitlines()
    out: list[str] = []
    in_list = False
    paragraph: list[str] = []

    def _flush_paragraph() -> None:
        if paragraph:
            out.append(f"<p>{_inline(' '.join(paragraph))}</p>")
            paragraph.clear()

    def _close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for raw in lines:
        line = raw.rstrip()

        if not line:
            _flush_paragraph()
            _close_list()
            continue

        if line.startswith("### "):
            _flush_paragraph(); _close_list()
            out.append(f"<h3>{_inline(line[4:])}</h3>")
        elif line.startswith("## "):
            _flush_paragraph(); _close_list()
            out.append(f"<h2>{_inline(line[3:])}</h2>")
        elif line.startswith("# "):
            _flush_paragraph(); _close_list()
            out.append(f"<h1>{_inline(line[2:])}</h1>")
        elif line.strip() == "---":
            _flush_paragraph(); _close_list()
            out.append("<hr>")
        elif line.startswith("- "):
            _flush_paragraph()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(line[2:])}</li>")
        else:
            paragraph.append(line)

    _flush_paragraph()
    _close_list()
    return "\n".join(out)


def render_apple_notes(draft: DraftIn, folder: str | None = None) -> AppleNotesPiece:
    md = render_markdown(draft)
    return AppleNotesPiece(
        topic_id=draft.topic_id,
        name=draft.headline,
        body_html=markdown_to_html(md),
        folder=folder,
    )
