"""Pipeline stage 3 — renderer.

Takes a writer `DraftBundle` and produces one of three target shapes:

- `markdown`     — plain markdown strings (useful for review / archival)
- `apple-notes`  — HTML bodies for the apple-notes MCP `create_note` tool
- `notion`       — lists of Notion blocks for the Notion API / MCP

The canonical intermediate is the markdown string produced by
`renderer.markdown.render_markdown`. The other two targets convert from
that string, so the layout is defined in one place.
"""
__version__ = "0.1.0"
