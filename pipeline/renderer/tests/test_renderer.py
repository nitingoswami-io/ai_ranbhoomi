"""Scaffold tests — markdown layout, HTML/blocks conversion, CLI end-to-end."""
from __future__ import annotations

from pathlib import Path

from renderer.cli import main
from renderer.html_out import markdown_to_html, render_apple_notes
from renderer.markdown import render_markdown
from renderer.models import AppleNotesBundle, DraftBundleIn, MarkdownBundle, NotionBundle
from renderer.notion_out import markdown_to_blocks, render_notion

FIXTURE = Path(__file__).parent / "fixtures" / "draft_bundle_sample.json"


def _load() -> DraftBundleIn:
    return DraftBundleIn.model_validate_json(FIXTURE.read_text())


# --- markdown layout -----------------------------------------------------

def test_markdown_layout_has_all_sections():
    d = _load().drafts[0]
    md = render_markdown(d)
    assert md.startswith(f"# {d.headline}\n")
    assert f"*{d.subhead}*" in md
    assert d.body_md.strip() in md
    assert "**Signals**" in md
    assert "**Sources**" in md
    assert "---" in md
    assert f"**Why this ranked:** {d.ranking_rationale}" in md
    for sig in d.key_signals:
        assert f"- {sig}" in md
    for s in d.sources:
        assert f"[{s.title}]({s.url})" in md


# --- markdown → HTML -----------------------------------------------------

def test_html_covers_headings_paragraphs_lists_hr():
    md = "# H1\n\n## H2\n\nA paragraph with **bold** and *italic* and a [link](https://example.com).\n\n- item one\n- item two\n\n---\n"
    html = markdown_to_html(md)
    assert "<h1>H1</h1>" in html
    assert "<h2>H2</h2>" in html
    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html
    assert '<a href="https://example.com">link</a>' in html
    assert "<ul>" in html and "<li>item one</li>" in html and "</ul>" in html
    assert "<hr>" in html


def test_html_escapes_user_text():
    html = markdown_to_html("A paragraph with <script>alert(1)</script>.\n")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_render_apple_notes_uses_headline_and_folder():
    d = _load().drafts[0]
    piece = render_apple_notes(d, folder="Daily Brief")
    assert piece.name == d.headline
    assert piece.folder == "Daily Brief"
    assert "<h1>" in piece.body_html
    assert d.topic_id == piece.topic_id


# --- markdown → Notion blocks --------------------------------------------

def test_notion_blocks_shape():
    md = "# H1\n\nA paragraph.\n\n- one\n- two\n\n---\n"
    blocks = markdown_to_blocks(md)
    types = [b["type"] for b in blocks]
    assert types == ["heading_1", "paragraph", "bulleted_list_item", "bulleted_list_item", "divider"]
    # Every non-divider block has rich_text spans
    for b in blocks:
        if b["type"] == "divider":
            continue
        assert b[b["type"]]["rich_text"], f"empty rich_text on {b['type']}"


def test_notion_inline_bold_italic_link_annotated():
    blocks = markdown_to_blocks("A **bold** and *italic* and [link](https://example.com) span.\n")
    spans = blocks[0]["paragraph"]["rich_text"]
    kinds = [(s.get("plain_text"), s["annotations"]["bold"], s["annotations"]["italic"], s.get("href")) for s in spans]
    assert ("bold", True, False, None) in kinds
    assert ("italic", False, True, None) in kinds
    assert ("link", False, False, "https://example.com") in kinds


def test_render_notion_wraps_draft():
    d = _load().drafts[0]
    piece = render_notion(d)
    assert piece.topic_id == d.topic_id
    assert piece.title == d.headline
    # first block is the h1 headline
    assert piece.blocks[0]["type"] == "heading_1"


# --- CLI -----------------------------------------------------------------

def test_cli_markdown(tmp_path):
    out = tmp_path / "out.json"
    rc = main(["-t", "markdown", "-i", str(FIXTURE), "-o", str(out)])
    assert rc == 0
    bundle = MarkdownBundle.model_validate_json(out.read_text())
    assert bundle.target == "markdown"
    assert len(bundle.pieces) == 1
    assert bundle.pieces[0].body.startswith("# ")


def test_cli_apple_notes(tmp_path):
    out = tmp_path / "out.json"
    rc = main(["-t", "apple-notes", "-i", str(FIXTURE), "-o", str(out), "--apple-notes-folder", "Daily Brief"])
    assert rc == 0
    bundle = AppleNotesBundle.model_validate_json(out.read_text())
    assert bundle.target == "apple-notes"
    assert bundle.pieces[0].folder == "Daily Brief"
    assert "<h1>" in bundle.pieces[0].body_html


def test_cli_notion(tmp_path):
    out = tmp_path / "out.json"
    rc = main(["-t", "notion", "-i", str(FIXTURE), "-o", str(out)])
    assert rc == 0
    bundle = NotionBundle.model_validate_json(out.read_text())
    assert bundle.target == "notion"
    assert bundle.pieces[0].blocks[0]["type"] == "heading_1"
