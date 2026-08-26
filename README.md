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

## Conventions

- Secrets stay out of git — see `.gitignore` (`.env`, `*.secret`, etc.). Rotate anything that was ever committed elsewhere.
- Python subprojects use their own `requirements.txt` or `pyproject.toml`; there's no repo-wide dependency file.
