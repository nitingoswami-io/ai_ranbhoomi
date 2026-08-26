# trend-radar

MCP server that surfaces trending AI/ML topics from Hacker News, arXiv, and Hugging Face Daily Papers. Deterministic ranking, LLM-normalized clustering (with lexical fallback), and a persistent novelty ledger so the same story doesn't get covered twice.

Stage 1 of a daily content pipeline: **trend-radar → Pydantic AI writer → renderer → Apple Notes / Notion**. This repo contains only stage 1.

**Why no Reddit?** Reddit's current API policy doesn't allow the script-app use case this server was designed to fit. The three remaining sources are all auth-free — nothing to configure to get real data flowing.

## What it exposes

Six MCP tools over stdio, callable from Claude Desktop via Docker MCP Toolkit:

| Tool | Purpose |
|---|---|
| `get_trending_topics` | Full pipeline. Returns ranked topics with per-source health. |
| `explain_ranking` | Every number that went into a topic's score. |
| `check_novelty` | Sanity-check a topic against the ledger before writing. |
| `mark_covered` | Record that a topic has been covered. Idempotent. |
| `list_covered` | Recent coverage history. |
| `get_source_config` | Diagnostic: which sources are configured, credentials present, scoring constants. |

## Quickstart — Docker MCP Toolkit

Run these once from this repo's root.

```bash
# 1. Build the image. The tag must match `image:` in docker/trend-radar.yaml.
docker build -t trend-radar:latest -f docker/Dockerfile .

# 2. Sanity-check the container as the runtime user. Should exit 0.
#    If you see "Permission denied", the Dockerfile got edited — restore
#    the --chown/--chmod on the COPY lines.
docker run --rm --entrypoint sh trend-radar:latest \
  -c 'timeout 2 python -u -m trend_radar < /dev/null; echo exit=$?'

# 3. Install the catalog entry.
mkdir -p ~/.docker/mcp/catalogs
cp docker/trend-radar.yaml ~/.docker/mcp/catalogs/

# 4. (Optional) Set the Anthropic key for LLM-quality clustering.
#    Without it, the server transparently falls back to lexical clustering —
#    still works, just less clean canonical titles.
docker mcp secret set trend-radar.anthropic_api_key

# 5. Attach to a profile. Substitute <PROFILE_ID> (create with
#    `docker mcp profile create <name>` if needed).
docker mcp profile server add <PROFILE_ID> --server file://trend-radar.yaml

# 6. Verify the snapshot has all six tools.
docker mcp profile show <PROFILE_ID> | awk '/name: trend-radar$/,/^secrets:$/'
```

Then in Docker Desktop → MCP Toolkit → your profile: `trend-radar` should show `6/6 enabled`.

Restart Claude Desktop so it re-lists tools. Ask it "what's trending in AI today?" — it should call `get_trending_topics`.

### Iterating

Rebuild the image after source changes, restart Claude Desktop. No profile re-add needed — the gateway resolves `trend-radar:latest` fresh each session.

If you add/rename a tool: update `src/trend_radar/server.py` **and** the `tools:` block in `docker/trend-radar.yaml`, rebuild, then:

```bash
cp docker/trend-radar.yaml ~/.docker/mcp/catalogs/
docker mcp profile server remove <PROFILE_ID> trend-radar
docker mcp profile server add    <PROFILE_ID> --server file://trend-radar.yaml
```

## Persistence

The container declares `VOLUME /data` — the SQLite ledger (`/data/trend_radar.db`) and rotating log file (`/data/trend_radar.log`) live there. Attach a named volume so coverage history survives container restarts:

```bash
# One-time volume creation
docker volume create trend-radar-data

# Docker MCP Toolkit auto-attaches the volume declared in the image.
# For a manual `docker run` (e.g. for debugging), pass it explicitly:
docker run --rm -i -v trend-radar-data:/data trend-radar:latest
```

## Verify with MCP Inspector

Between build and hooking up Claude, spot-check the server with the official inspector:

```bash
npx @modelcontextprotocol/inspector \
  docker run --rm -i -v trend-radar-data:/data \
    -e ANTHROPIC_API_KEY \
    trend-radar:latest
```

The Inspector UI opens in a browser. `tools/list` should show six tools; try `get_source_config` first, then `get_trending_topics`.

For a purely local check (no Docker), see the Development section below.

## End-to-end probe

Skip Claude, test the toolkit gateway directly. Should print every tool name across every attached server:

```bash
( printf '%s\n' \
    '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"0.0.0"}}}' \
    '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
    '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'; \
  sleep 15 ) \
  | docker mcp gateway run --profile <PROFILE_ID> 2>/dev/null \
  | grep -o '"name":"[^"]*"' | sort -u
```

## Development

Local dev without Docker — useful for iterating on adapters or scoring.

```bash
uv sync --extra dev
cp .env.example .env    # then edit .env
# For local dev you probably want:
#   TREND_RADAR_DB=./data/trend_radar.db
mkdir -p data

# Boot on stdio (Ctrl-C to exit)
uv run python -m trend_radar

# Test with Inspector
npx @modelcontextprotocol/inspector uv run python -m trend_radar

# Run the test suite
uv run pytest
```

## Configuration

All settings load from environment (via `.env` locally, `docker mcp secret set` in production). Nested delimiter is `__` per pydantic-settings — override scoring constants like `TREND_RADAR_SCORING__CORROBORATION_PER_SOURCE=0.20`.

| Env var | Default | Purpose |
|---|---|---|
| `TREND_RADAR_DB` | `/data/trend_radar.db` | SQLite path — ledger + run cache |
| `TREND_RADAR_LOG_DIR` | (derives from db_path.parent) | Log file directory |
| `TREND_RADAR_MAX_ITEMS_PER_TOPIC` | `5` | Top-N items retained per topic in the run cache |
| `TREND_RADAR_LLM_MODEL` | `anthropic:claude-sonnet-4-6` | Model for clustering when key present |
| `TREND_RADAR_EMBED_MODEL` | (unset) | Embedding model — reserved; not implemented yet |
| `TREND_RADAR_SCORING__*` | see `config.py` | Override any scoring constant |
| `ANTHROPIC_API_KEY` | — | Enables LLM clustering; absent → lexical fallback |
| `LOGFIRE_TOKEN` | — | Optional telemetry |

Call `get_source_config` from Claude at any time to see what's currently loaded.

## Ranking notes with 3 sources

- **HN and HF are percentile-ranked.** HN uses `points`, HF uses `upvotes` — both get sorted within their own source pool and assigned `(rank + 0.5) / n`. A high-upvote HF paper competes for rank alongside a high-point HN thread without either drowning the other out.
- **arXiv is baselined.** Paper listings carry no score signal (no votes, no discussion count), so every arXiv item gets a fixed `baseline_source_percentile = 0.5`. arXiv items rise via corroboration when HN or HF also picks the same paper up.
- **Corroboration cap** is `0.30` (was `0.45` when Reddit was in the mix). Formula unchanged: `(distinct_sources - 1) * corroboration_per_source`, clamped to the cap. Max value is `(3-1)*0.15 = 0.30`.
- Cross-source topics (an arXiv paper that gets discussed on HN or shows up on the HF daily-papers list) still dominate rankings, which is the intended behavior.

## Debugging checklist

Symptom → probable cause → fix.

| Symptom | Cause | Fix |
|---|---|---|
| Row is greyed out in Docker Desktop | Container failed to start | `docker run --rm trend-radar:latest` — if you see `Permission denied`, restore the `--chown --chmod` on the Dockerfile COPY lines |
| Row shows but says `0/N` tools | `tools:` block missing / stale in the catalog YAML | Re-copy `docker/trend-radar.yaml` to `~/.docker/mcp/catalogs/`, remove + re-add on the profile |
| Tools list works, tool call parses as garbage | Something wrote to stdout | Grep for bare `print(` — everything goes to stderr (this codebase is clean, but a stray `print` in an adapter would break it) |
| `docker mcp profile server add ... --server docker://...` fails | Image lacks MCP OCI labels | Use `--server file://<yaml>` — the recommended path here |
| Container starts, exits immediately | Buffered stdio or shell-form ENTRYPOINT ate stdin | Confirm `ENV PYTHONUNBUFFERED=1` and exec-form `ENTRYPOINT ["python", "-u", ...]` (present in the shipped Dockerfile) |
| Claude Desktop doesn't see the new tool after a rebuild | Client cached tools at startup | Restart Claude Desktop |
| `clustering_method` is always `"lexical"` | `ANTHROPIC_API_KEY` not set | `docker mcp secret set trend-radar.anthropic_api_key` |
| Some `source_health[*]` entry is `ok=false` | Upstream is down or rate-limiting | Retry; if persistent, check the source's status page. HN Algolia, arXiv, and Hugging Face are all no-auth so it's not a credential issue. |

## Evals

Ten read-only evaluations exercising multi-tool reasoning across all five read tools live in `evals/`. See `evals/README.md` for how to run them.

```bash
export ANTHROPIC_API_KEY=sk-...
npx mcp-evals evals/trend-radar.evals.yaml -- docker run --rm -i -v trend-radar-data:/data trend-radar:latest
```

## Phases

- [x] 1 — Scaffold, models, config, stub tools that list correctly
- [x] 2 — Source adapters (HN, arXiv, HF) + partial-failure handling + rate limiting
- [x] 3 — Clustering (Pydantic AI + rapidfuzz fallback), scoring, SQLite ledger + run cache
- [x] 4 — Tool wiring end-to-end, error envelopes, lifespan management
- [x] 5 — Dockerfile, MCP_DOCKER catalog + registry, README hookup
- [x] 6 — Evaluation set (10 read-only, multi-tool)
