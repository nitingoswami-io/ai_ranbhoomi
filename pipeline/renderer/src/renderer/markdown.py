"""Canonical markdown layout for a Draft.

Single source of truth for how a Draft is laid out as a post. The
apple-notes and notion targets convert from this string, so layout
changes here propagate to every target.
"""
from __future__ import annotations

from renderer.models import DraftIn


def render_markdown(draft: DraftIn) -> str:
    """Render a Draft as a full markdown post."""
    parts: list[str] = [
        f"# {draft.headline}",
        "",
        f"*{draft.subhead}*",
        "",
        draft.body_md.strip(),
        "",
    ]

    if draft.key_signals:
        parts.append("**Signals**")
        parts.append("")
        parts.extend(f"- {sig}" for sig in draft.key_signals)
        parts.append("")

    if draft.sources:
        parts.append("**Sources**")
        parts.append("")
        for s in draft.sources:
            parts.append(f"- [{s.title}]({s.url}) — *{s.signal}* ({s.source})")
        parts.append("")

    parts.append("---")
    parts.append("")
    parts.append(f"**Why this ranked:** {draft.ranking_rationale}")

    return "\n".join(parts) + "\n"
