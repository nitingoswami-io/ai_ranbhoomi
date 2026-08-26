# pipeline-renderer

Stage 3 of the content pipeline. Takes writer's `DraftBundle` in, emits a target-specific bundle out. Three targets today: `markdown` (review/archival), `apple-notes` (HTML for `apple-notes.create_note`), `notion` (Notion API blocks).

The canonical layout lives in `renderer/markdown.py` — the other two targets convert from that markdown, so a layout change propagates everywhere.

## Install

```bash
cd pipeline/renderer
uv sync --extra dev
```

## Run

```bash
# Piped from the writer:
python -m writer -i trending.json | python -m renderer -t apple-notes --apple-notes-folder "Daily Brief"

# Or from a file:
uv run python -m renderer -t notion -i drafts.json -o notion.json

# Markdown (default review target):
uv run python -m renderer -t markdown -i drafts.json
```

## Targets

| Target | Output shape | Downstream |
|---|---|---|
| `markdown` | `MarkdownBundle` — full post as one markdown string per piece | Review, git-committed archive, `pandoc` |
| `apple-notes` | `AppleNotesBundle` — `{name, body_html, folder}` per piece | `apple-notes.create_note(name=name, body=body_html, folder=folder)` |
| `notion` | `NotionBundle` — `{title, blocks}` per piece | Notion API `POST /pages` (children = blocks) |

## Contract

- **Input:** `DraftBundleIn` — subset mirror of `writer.models.DraftBundle`. Extra fields ignored.
- **Output:** exactly one of `MarkdownBundle | AppleNotesBundle | NotionBundle`, discriminated by the `target` field. Each piece type is shaped for its downstream delivery — no unions to unwrap.

## Markdown subset supported

The hand-rolled parser covers what a Draft actually produces:

- Headings: `#`, `##`, `###`
- Paragraphs (blank-line-separated)
- Bulleted lists (`- `)
- Horizontal rule (`---`)
- Inline: `**bold**`, `*italic*`, `[text](url)`

Notion output emits inline styling as `rich_text` spans with per-span `annotations`. HTML output escapes user text before applying inline patterns.

If the writer's output grows to need tables, code blocks, or nested lists, swap the hand-rolled parser for `markdown-it-py` — the module boundary (`markdown_to_html`, `markdown_to_blocks`) is already the right shape.

## Tests

```bash
uv run pytest
```

Tests cover the markdown layout, HTML conversion (including escape safety), Notion block shape (including inline annotations), and CLI end-to-end for all three targets.

## What's next

- **Delivery stage.** The renderer stops at emitting ready-to-send payloads; a separate script (or your `Makefile`) should read a target-specific bundle and push to `apple-notes` MCP or Notion. After successful delivery, call `trend-radar.mark_covered(topic_id=...)` to close the loop.
- **Richer markdown.** If the writer starts emitting code blocks or nested lists, `markdown-it-py` is the right upgrade.
- **Style themes.** Consider a `--style` flag once the layout has variants (e.g. compact for X/Twitter, expanded for newsletter).
