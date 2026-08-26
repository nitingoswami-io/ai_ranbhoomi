# Build prompt: `trend-radar` MCP server

> Paste this whole file into Claude Code as the opening message. Work through it in phases and stop for review at each checkpoint.

---

## Role and objective

You are building a production-quality MCP server called **`trend-radar`**. It detects which AI/ML topics are trending right now across free, ToS-compliant sources, ranks them, guards against repeating topics I've already covered, and exposes all of this as MCP tools over **stdio** so it can run inside Docker Desktop's MCP Toolkit (MCP_DOCKER) and be called from Claude Desktop.

This server is stage 1 of a daily content pipeline: `trend-radar` → Pydantic AI writer agent → renderer → Apple Notes / Notion / local drive. You are building **only** `trend-radar`. Do not build the writer, the renderer, or any publishing logic.

---

## Stack decisions (do not deviate without telling me first)

| Concern | Choice | Why |
|---|---|---|
| Protocol layer | `mcp[cli]` Python SDK, **FastMCP** (`from mcp.server.fastmcp import FastMCP`) | This is what an MCP server is built with. Pydantic AI is an MCP *client* framework — it does not serve MCP. |
| Typed I/O | **Pydantic v2** models on every tool input and output | Gives us `outputSchema` for free and is the contract the downstream Pydantic AI agent consumes |
| LLM work inside the server | **Pydantic AI** — used for exactly one job, topic normalization and clustering (see §4) | This is where Pydantic AI genuinely earns its place. Do not use it for ranking. |
| Transport | **stdio only** | Required by Docker MCP Toolkit |
| HTTP | `httpx` async, explicit timeouts | |
| Storage | SQLite via `aiosqlite`, DB path from `TREND_RADAR_DB` env, default `/data/trend_radar.db` | Must be volume-mounted so the ledger survives container restarts |
| Package manager | `uv` | |
| Python | 3.12 | |
| Observability | `logfire` if `LOGFIRE_TOKEN` is set, otherwise silent no-op | Optional, never a hard dependency |

**Read these before writing code:**
- `https://raw.githubusercontent.com/modelcontextprotocol/python-sdk/main/README.md`
- `https://modelcontextprotocol.io/sitemap.xml`, then the transports and tools spec pages with `.md` suffix
- Pydantic AI docs for `Agent`, `output_type`, and output validators

---

## 1. Sources to ingest

Four adapters, one interface each: `async def fetch(lookback_hours: int) -> list[RawItem]`.

**Reddit** — official API, OAuth2 client-credentials, registered *script* app. Credentials from `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET`. Set a descriptive `User-Agent` in the form `trend-radar/0.1 by /u/<username>`. Subreddits configurable via `TREND_RADAR_SUBREDDITS`, default: `LocalLLaMA, MachineLearning, singularity, OpenAI, ClaudeAI, LangChain, artificial`. Pull `/r/<sub>/hot` and `/r/<sub>/top?t=day`.

**Hacker News** — Algolia search API (`http://hn.algolia.com/api/v1/search_by_date`), no auth, no key. Query for AI-related terms and filter by `points > 20`.

**arXiv** — Atom API, categories `cs.AI`, `cs.LG`, `cs.CL`, sorted by submission date. No auth.

**Hugging Face** — Daily Papers endpoint. No auth.

**Explicitly out of scope: X/Twitter.** The read API is pay-per-use at ~$0.005/post-read with no free tier as of Feb 2026. Do not add it, do not add a placeholder adapter for it, and do not scrape it. If you think you've found a free route, tell me instead of implementing it.

**No HTML scraping anywhere.** Official APIs and feeds only. Respect rate limits with a token-bucket limiter per source.

**Partial failure is normal, not fatal.** If Reddit is down, the run continues with the other three and the tool response includes a `source_health` field listing which adapters succeeded, which failed, and why. Never let one dead source produce an empty result set or an exception at the MCP boundary.

---

## 2. Data models

Define these in `models.py`. These are the contract — the downstream writer agent depends on them, so treat field names as API surface.

```python
class RawItem(BaseModel):        # what an adapter returns
    source: Literal["reddit", "hackernews", "arxiv", "huggingface"]
    source_id: str
    title: str
    url: HttpUrl
    permalink: HttpUrl | None    # discussion thread, distinct from the linked artifact
    raw_score: float             # upvotes / points / 0 for arXiv+HF
    comment_count: int | None
    created_at: datetime         # tz-aware UTC, always
    body_excerpt: str | None     # first ~500 chars where available

class NormalizedTopic(BaseModel):   # Pydantic AI output — see §4
    canonical_title: str            # max 80 chars
    one_line: str                   # max 160 chars, plain language
    entities: list[str]             # models, papers, companies, libraries named
    tags: list[str]                 # max 5, lowercase

class ScoreBreakdown(BaseModel):
    velocity: float
    source_percentile: float
    corroboration_bonus: float
    novelty_multiplier: float
    final_score: float
    explanation: str              # one human-readable sentence

class RankedTopic(BaseModel):
    topic_id: str                 # stable hash of canonical_title
    topic: NormalizedTopic
    items: list[RawItem]          # every item clustered into this topic
    distinct_sources: int
    score: ScoreBreakdown
    suppressed: bool              # true if novelty gate rejected it
    suppression_reason: str | None

class NoveltyResult(BaseModel):
    is_novel: bool
    max_similarity: float
    closest_match_title: str | None
    closest_match_date: date | None

class CoveredTopic(BaseModel):
    topic_id: str
    canonical_title: str
    covered_on: date
    post_url: HttpUrl | None
    notes: str | None
```

---

## 3. Scoring — deterministic, no LLM

Ranking must be pure Python and fully explainable. When I ask "why did it pick that?", the answer must be arithmetic, not vibes.

1. **Velocity**: `raw_score / (age_hours + 2) ** 1.5`. HN-style gravity.
2. **Cross-source normalization**: raw Reddit upvotes and HN points are not comparable. Convert each item's velocity to a **percentile within its own source** for that run. arXiv and HF have no score signal — assign them a fixed baseline percentile (0.5) and let corroboration do the work.
3. **Corroboration**: `+0.15` per additional distinct source in the cluster, capped at `+0.45`. A topic appearing on Reddit *and* HN *and* arXiv is the strongest signal available.
4. **Novelty multiplier**: from §5. Multiply, don't add.
5. `final_score = (source_percentile + corroboration_bonus) * novelty_multiplier`

Every constant above goes in a `ScoringConfig` Pydantic settings model, overridable by env. No magic numbers inline.

---

## 4. Where Pydantic AI is used

**One job: turning heterogeneous raw items into canonical topics and clustering them.** "Anthropic releases Claude Opus 5" on Reddit, "Opus 5 benchmarks" on HN, and an arXiv paper on the same architecture are one topic, not three. Lexical matching gets this wrong constantly.

```python
normalizer = Agent(
    model=os.getenv("TREND_RADAR_MODEL", "anthropic:claude-sonnet-4-6"),
    output_type=list[TopicCluster],
    system_prompt=...,
)
```

Requirements:
- Batch the whole candidate set into **one** call, not one call per item. Cost and latency both matter for a daily cron.
- Use an `@normalizer.output_validator` with `ModelRetry` to enforce: every input item id appears in exactly one cluster, no invented item ids, `canonical_title` under 80 chars. Retry limit 2, then fail cleanly.
- **Mandatory graceful degradation**: if `ANTHROPIC_API_KEY` is absent or the call fails after retries, fall back to lexical clustering with `rapidfuzz.fuzz.token_set_ratio` at threshold 82. The server must remain fully functional with zero LLM access — it just clusters worse. Surface which path was used in a `clustering_method` field.

Do not use Pydantic AI anywhere else in this server.

---

## 5. The novelty ledger

SQLite table `covered_topics(topic_id PK, canonical_title, one_line, covered_on, post_url, notes, embedding BLOB NULL)`.

Novelty check against the trailing 90 days:
- **If an embedding provider is configured** (`TREND_RADAR_EMBED_MODEL`): cosine similarity, suppress above `0.85`.
- **Otherwise**: `rapidfuzz.token_set_ratio` on `canonical_title + one_line`, suppress above `88`.

Novelty multiplier: `1.0` when clearly novel, scaling linearly to `0.0` as similarity approaches the threshold. Suppressed topics are **still returned** in results with `suppressed=true` and a reason — I want to see what was filtered and why, not have it silently vanish.

Schema migrations: simple `user_version` pragma stepping. Create the DB and tables on first run if absent.

---

## 6. Tools to expose — exactly six

Follow MCP tool-design conventions: action-oriented names, concise descriptions, constraints and examples in field descriptions, correct annotations, actionable error messages.

| Tool | Annotations | Behavior |
|---|---|---|
| `get_trending_topics(lookback_hours=24, limit=15, include_suppressed=True, sources=None)` | readOnly, openWorld | The main one. Full pipeline: ingest → normalize/cluster → score → novelty-gate → rank. Returns `TrendingResult` with `topics`, `source_health`, `clustering_method`, `run_id`. |
| `explain_ranking(topic_id)` | readOnly | Full `ScoreBreakdown` plus every contributing `RawItem` with its individual numbers. Must be answerable from the cached last run — do not re-fetch. |
| `check_novelty(title, one_line=None)` | readOnly | Ad-hoc check of an arbitrary topic string against the ledger. Lets me sanity-check an idea before committing. |
| `mark_covered(topic_id=None, canonical_title=None, post_url=None, notes=None)` | idempotent, **not** readOnly | Writes to the ledger. Accepts either a `topic_id` from a recent run or a free-text title. Idempotent on `topic_id`. |
| `list_covered(days=90, limit=100)` | readOnly | Recent coverage history, newest first. |
| `get_source_config()` | readOnly | Which sources are configured, which have credentials present, current subreddit list, scoring constants in effect. Diagnostic tool — makes misconfiguration debuggable from inside Claude. |

Cache the last run's full `RankedTopic` set (in SQLite, keyed by `run_id`) so `explain_ranking` and `mark_covered` work without re-ingesting.

**Error handling at the MCP boundary**: never let a raw exception escape. Every failure returns a structured error with a concrete next step — e.g. `"Reddit auth failed (401). Check REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET are set as Docker MCP secrets: docker mcp secret set REDDIT_CLIENT_ID"`.

---

## 7. Packaging for MCP_DOCKER

- `Dockerfile`, multi-stage, `uv` install, non-root user, `CMD ["python", "-m", "trend_radar.server"]`, stdio.
- Declare `VOLUME /data` and document that the ledger lives there.
- Custom catalog YAML entry and registry entry for the Docker MCP Toolkit, following the same structure as a standard custom server.
- Secrets via `docker mcp secret set` — never baked into the image, never in the catalog YAML, never logged. Redact credential values from all log output.
- `README.md` with: exact `docker mcp` commands to build, register, set secrets, and enable; how to get Reddit script-app credentials; how to verify with MCP Inspector; how to point Claude Desktop at it.

---

## 8. Testing

- `pytest` + `pytest-asyncio` + `respx` for HTTP mocking. **Zero live network calls in the test suite.**
- Recorded fixtures for each of the four sources, including a rate-limited response and a 500.
- Scoring tests are exact-value assertions against hand-computed expectations — this is the part I most need to trust.
- Clustering tests cover both the LLM path (mocked with `pydantic_ai.models.test.TestModel`) and the lexical fallback.
- A test that the server starts, lists exactly six tools, and every tool has a non-empty description and a valid output schema.
- One `scripts/smoke.py` that hits real APIs, prints today's top 10 with score breakdowns, and is run manually only.

---

## 9. Phased delivery — stop at each checkpoint

1. **Scaffold + models + config.** Project layout, all Pydantic models, settings, empty tool stubs that return typed placeholder data. Server starts and lists six tools. → *stop for review*
2. **Source adapters + tests.** All four, with fixtures, rate limiting, partial-failure handling. → *stop for review*
3. **Clustering + scoring + ledger.** Pydantic AI normalizer, lexical fallback, scoring engine, SQLite. → *stop for review*
4. **Tool wiring + error handling.** All six tools live end to end. Verified in MCP Inspector. → *stop for review*
5. **Docker + catalog + README.** Registered in MCP_DOCKER, callable from Claude Desktop. → *stop for review*
6. **Evaluation set.** 10 read-only questions per the MCP evaluation format, exercising multi-tool reasoning (e.g. "which topic in the last run had the highest corroboration bonus, and how many distinct sources contributed?"). → *done*

---

## Non-negotiables

- No X/Twitter. No HTML scraping. No credentials in code, catalog files, or logs.
- Server stays fully functional with no LLM API key present.
- Ranking never depends on an LLM.
- Six tools. If you think a seventh is needed, ask me first.
- All datetimes tz-aware UTC.

## Start here

Read the MCP Python SDK README and the transport spec, then propose the project layout and the `models.py` contents. Do not write implementation code until I've approved phase 1.
