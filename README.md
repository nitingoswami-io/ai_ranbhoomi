# ai_ranbhoomi

Experimentation arena for AI tooling — Claude skills, MCP servers, and content pipelines. This repo unifies work that previously lived across separate projects.

## Layout

| Path | What it is |
|---|---|
| [`DIY-claude-skills/`](DIY-claude-skills/) | Hand-rolled Claude Code skills (`.skill` files) — Instagram quote cards, LinkedIn tech carousels, payer-in-the-loop, etc. |
| [`mcp_server/`](mcp_server/README.md) | Custom MCP servers packaged for the Docker MCP Toolkit. See sub-projects below. |
| `pipeline/` | Placeholder for the end-to-end content pipeline that stitches the pieces together. |

### MCP servers

| Server | Purpose |
|---|---|
| [`mcp_server/apple-notes/`](mcp_server/apple-notes/README.md) | Read/write Apple Notes from Claude via a macOS-host bridge (AppleScript can't run inside a container). |
| [`mcp_server/dice/`](mcp_server/dice/) | Reference example — dice roller used as the walkthrough server in [`mcp_server/README.md`](mcp_server/README.md). |
| [`mcp_server/insta-quotes/`](mcp_server/insta-quotes/) | Serves founder quotes; feeds the `instagram-quote-card` skill. |
| [`mcp_server/trend-radar-mcp/`](mcp_server/trend-radar-mcp/README.md) | Ranks trending AI/ML topics from Hacker News, arXiv, and Hugging Face Daily Papers. Stage 1 of the content pipeline. |

## Getting started

Each subproject stands on its own — start from the relevant sub-README:

- **Building an MCP server locally** → [`mcp_server/README.md`](mcp_server/README.md) (end-to-end walkthrough with the dice server, plus Docker MCP Toolkit setup).
- **Running trend-radar** → [`mcp_server/trend-radar-mcp/README.md`](mcp_server/trend-radar-mcp/README.md).
- **Apple Notes bridge** → [`mcp_server/apple-notes/README.md`](mcp_server/apple-notes/README.md).

## Composed flows

### `insta-quotes` → `instagram-quote-card`

The two are designed to run back-to-back. `insta-quotes` is the **research** stage; `instagram-quote-card` is the **render** stage. Claude sits in the middle and does the picking.

1. **Caller invokes `insta-quotes.create_quote(theme, ...)`.** The MCP tool picks a founder from a curated roster (`founders.json`), searches DuckDuckGo + Wikiquote for their interviews, extracts quoted spans from the pages (Wikiquote `<li>` → `<blockquote>` → attributed `<p>` spans, in that order), and returns a bundle of sources with excerpts plus a synthesis directive. The server does **no** LLM synthesis itself — no API keys, no model calls.
2. **Claude reads the excerpts and chooses a real, verbatim quote.** The `instagram-quote-card` skill's hard rule is *never render words the person didn't actually say*, so the model must pull the exact quoted span from an excerpt — not paraphrase, not compose "in the voice of". If nothing usable surfaces, the correct move is to stop and say so.
3. **Claude invokes the `instagram-quote-card` skill** with the verbatim quote, attribution, source, and a `verification` string (mandatory — the renderer refuses to run without it). The skill emits the 1080px card (or a 2/3-slide carousel) plus caption, hashtags, and alt text.

The seam between the two is intentional: `insta-quotes` guarantees grounding material with URLs, and `instagram-quote-card` guarantees nothing ships without attribution the caller can defend. Neither trusts the other to enforce it.

## Conventions

- Secrets stay out of git — see `.gitignore` (`.env`, `*.secret`, etc.). Rotate anything that was ever committed elsewhere.
- Python subprojects use their own `requirements.txt` or `pyproject.toml`; there's no repo-wide dependency file.
