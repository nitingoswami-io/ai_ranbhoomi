"""Instagram Post content writer MCP server.

Exposes one tool: `create_quote`. Given a theme (e.g. "perseverance",
"failure", "first-customer"), the tool picks a fitting founder from a
curated roster, searches the web for interviews and articles about that
founder's journey, extracts source snippets, and returns everything the
calling LLM needs to compose an Instagram-ready quote card:

    - a chosen founder + why they fit the theme
    - 3-5 source snippets with URLs (grounding, so nothing is fabricated)
    - a synthesis directive with the exact output shape to emit
    - a caption + hashtag scaffold and an image prompt template

The tool intentionally does NOT do LLM synthesis itself — the calling
model (Claude, in this repo's case) reads the source snippets and writes
the final quote, caption, and image prompt. That keeps the server free
of API keys and lets any MCP-speaking model use it.

SDK NOTE: targets `mcp >= 2.0` (`MCPServer`). If pinned to 1.x, swap the
import to `from mcp.server.fastmcp import FastMCP` and use `FastMCP(...)`.

STDIO RULE: stdout belongs to JSON-RPC. All diagnostics go to stderr.
"""

from __future__ import annotations

import json
import random
import re
import sys
import urllib.parse
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from mcp.server.mcpserver import MCPServer

mcp = MCPServer(name="insta-quotes", version="0.1.0")

ROSTER_PATH = Path(__file__).parent / "founders.json"
ROSTER = json.loads(ROSTER_PATH.read_text())

# A realistic UA — DuckDuckGo's HTML endpoint returns an empty page for
# obviously-bot user agents like `python-httpx`.
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}

SEARCH_URL = "https://html.duckduckgo.com/html/"
FETCH_TIMEOUT = 12.0
MAX_RESULTS = 8
MAX_PAGES_TO_FETCH = 5
SNIPPET_CHAR_CAP = 600
MIN_QUOTE_CHARS = 40         # skip tiny quoted fragments ("hi", "yes", etc.)
MIN_QUOTE_WORDS = 6

# Words that flag a paragraph as *about* a quote being spoken, so a matching <p> is much
# more likely to actually contain the founder's words rather than biographical prose.
ATTRIBUTION_MARKERS = (
    "said", "says", "told", "recalls", "recalled", "wrote",
    "explained", "added", "asked", "noted", "put it", "quipped",
)


def _pick_founder(theme: str, override: str | None) -> dict:
    if override:
        return {"name": override, "context": f"caller-specified for theme '{theme}'"}
    roster = ROSTER["themes"].get(theme.lower().strip()) or ROSTER["fallback"]
    return random.choice(roster)


def _search(query: str) -> list[dict]:
    """DuckDuckGo HTML search. Returns [{title, url, snippet}, ...]."""
    resp = httpx.post(
        SEARCH_URL,
        data={"q": query},
        headers=HEADERS,
        timeout=FETCH_TIMEOUT,
        follow_redirects=True,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    for r in soup.select("div.result")[:MAX_RESULTS]:
        a = r.select_one("a.result__a")
        s = r.select_one("a.result__snippet") or r.select_one(".result__snippet")
        if not a:
            continue
        href = a.get("href", "")
        # DuckDuckGo wraps URLs in a redirect — unwrap when possible.
        parsed = urllib.parse.urlparse(href)
        qs = urllib.parse.parse_qs(parsed.query)
        real = qs.get("uddg", [href])[0]
        results.append(
            {
                "title": a.get_text(strip=True),
                "url": real,
                "snippet": s.get_text(" ", strip=True) if s else "",
            }
        )
    return results


# Character classes for typographic + straight quotation marks. Written as separate
# alternations so we correctly match opening→closing pairs and don't span an unrelated pair.
_QUOTE_PATTERNS = [
    (r"“", r"”"),   # “ ”  curly double
    (r"‘", r"’"),   # ‘ ’  curly single (used less; can wrap phrases)
    (r'"', r'"'),             # "…"  straight double
    (r"«", r"»"),   # « »  guillemets
]


def _extract_quoted_spans(text: str) -> list[str]:
    """Pull out substrings between matching quotation marks — the founder's actual words.

    Applies to text already flattened out of HTML (one line, whitespace collapsed). We
    require the span be at least MIN_QUOTE_CHARS long to filter out titles-in-quotes,
    slang, and single-word emphases.
    """
    found: list[str] = []
    for open_c, close_c in _QUOTE_PATTERNS:
        # Non-greedy match up to the closing mark; skip lines that don't contain both.
        for m in re.finditer(rf"{open_c}([^{open_c}{close_c}]{{1,{SNIPPET_CHAR_CAP}}}?){close_c}", text):
            span = m.group(1).strip()
            if len(span) >= MIN_QUOTE_CHARS and len(span.split()) >= MIN_QUOTE_WORDS:
                found.append(span)
    return found


def _parse_wikiquote(soup: BeautifulSoup, must_contain: str) -> list[str]:
    """Wikiquote structures quotes as <ul><li> under section headings — the standard
    <p>-scraper skips these entirely, which is why Bezos's Wikiquote page previously
    returned only its biographical intro paragraph and none of the actual quotes.
    """
    hits: list[str] = []
    tokens = [t for t in re.split(r"\s+", must_contain) if len(t) > 2]
    for li in soup.select("div.mw-parser-output > ul > li, .mw-parser-output ul li"):
        # A wikiquote <li> has the quote text at the top and citation <ul>/<dl> children
        # after. Drop the children so we don't pull the citation into the quote.
        for child in li.find_all(["ul", "dl"]):
            child.decompose()
        text = re.sub(r"\s+", " ", li.get_text(" ", strip=True))
        # Wikiquote often wraps the actual sentence in “...” — prefer the wrapped span
        # when present, since it's the exact spoken line.
        spans = _extract_quoted_spans(text)
        candidates = spans if spans else [text]
        for cand in candidates:
            if len(cand) < MIN_QUOTE_CHARS or len(cand.split()) < MIN_QUOTE_WORDS:
                continue
            # Wikiquote entries are attributed to the page's subject by definition, so we
            # don't need must_contain to appear IN the quote. But if there are multiple
            # people quoted (mixed pages), skip lines that mention someone else prominently.
            if not any(tok.lower() in cand.lower() for tok in tokens):
                hits.append(cand[:SNIPPET_CHAR_CAP])
            else:
                hits.append(cand[:SNIPPET_CHAR_CAP])
        if len(hits) >= 8:
            break
    return hits


def _fetch_paragraphs(url: str, must_contain: str) -> list[str]:
    """Fetch a page and return the strongest quoted material we can find.

    Priority order — each source of quotes is tried in turn, we return the first that
    produces at least one hit:
      1. Wikiquote: `<ul><li>` entries (the whole page is quotes by design)
      2. `<blockquote>` tags: publications use these for pull quotes
      3. Quoted spans inside `<p>` tags near an attribution verb ("said", "told", …)
      4. Quoted spans inside any `<p>` mentioning the founder
      5. Full paragraphs mentioning the founder — the old behaviour, kept as a floor

    Levels 1–4 return actual spoken words; level 5 returns descriptive prose about the
    founder. The downstream extractor prefers the earlier levels because the surrounding
    excerpt already looks like a direct quote.
    """
    try:
        resp = httpx.get(
            url, headers=HEADERS, timeout=FETCH_TIMEOUT, follow_redirects=True
        )
        resp.raise_for_status()
    except (httpx.HTTPError, httpx.InvalidURL):
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    tokens = [t for t in re.split(r"\s+", must_contain) if len(t) > 2]

    # Level 1 — Wikiquote list entries.
    if "wikiquote.org" in url:
        wq_hits = _parse_wikiquote(soup, must_contain)
        if wq_hits:
            return wq_hits[:8]

    # Level 2 — <blockquote> tags. These are almost always real pull quotes.
    bq_hits: list[str] = []
    for bq in soup.find_all("blockquote"):
        text = re.sub(r"\s+", " ", bq.get_text(" ", strip=True))
        if len(text) < MIN_QUOTE_CHARS:
            continue
        # Prefer the inner quoted span if the blockquote wraps quote marks; otherwise take
        # the blockquote text itself (many CMSes render pull quotes without literal marks).
        spans = _extract_quoted_spans(text)
        for cand in (spans or [text]):
            bq_hits.append(cand[:SNIPPET_CHAR_CAP])
        if len(bq_hits) >= 6:
            break
    if bq_hits:
        return bq_hits

    # Levels 3 + 4 — quoted spans inside <p>. First pass keeps only paragraphs that also
    # contain an attribution marker (much higher precision); second pass relaxes that
    # requirement if we came up empty.
    strong: list[str] = []
    weak: list[str] = []
    for p in soup.find_all("p"):
        text = re.sub(r"\s+", " ", p.get_text(" ", strip=True))
        if len(text) < MIN_QUOTE_CHARS:
            continue
        if not any(tok.lower() in text.lower() for tok in tokens):
            continue
        spans = _extract_quoted_spans(text)
        if not spans:
            continue
        low = text.lower()
        bucket = strong if any(m in low for m in ATTRIBUTION_MARKERS) else weak
        for span in spans:
            bucket.append(span[:SNIPPET_CHAR_CAP])
        if len(strong) >= 6:
            break
    if strong:
        return strong
    if weak:
        return weak[:6]

    # Level 5 — old behaviour as a floor: whole paragraphs, so the downstream extractor at
    # least has SOMETHING to work with. It will likely return null on these, which is
    # correct: verify.py catches it.
    fallback: list[str] = []
    for p in soup.find_all(["p", "blockquote"]):
        text = re.sub(r"\s+", " ", p.get_text(" ", strip=True))
        if len(text) < MIN_QUOTE_CHARS:
            continue
        if any(tok.lower() in text.lower() for tok in tokens):
            fallback.append(text[:SNIPPET_CHAR_CAP])
        if len(fallback) >= 6:
            break
    return fallback


@mcp.tool()
def create_quote(
    theme: str,
    founder: str | None = None,
    count: int = 1,
) -> dict:
    """Gather source material for an Instagram-ready founder quote.

    Picks a founder that fits the given theme, searches the web for their
    interviews and journey stories, extracts grounding snippets, and
    returns a synthesis brief the calling LLM should use to compose the
    final post.

    Args:
        theme: Theme keyword. Curated themes include "perseverance",
            "failure", "first-customer", "innovation", "discipline",
            "vision", "risk", "team", "customer-obsession". Unknown
            themes fall back to a general roster.
        founder: Optional. Force a specific founder (e.g. "Nithin Kamath")
            instead of theme-based selection. The theme still shapes the
            search query.
        count: How many Instagram post variants the calling LLM should
            produce from the returned material. Defaults to 1.

    Returns:
        A dict with the chosen founder, source snippets with URLs, and a
        synthesis directive that specifies the exact output shape
        (quote_text, attribution, source_urls, instagram_caption,
        hashtags, image_prompt) for the calling LLM to emit.
    """
    theme = (theme or "").strip() or "perseverance"
    picked = _pick_founder(theme, founder)
    name = picked["name"]
    context = picked["context"]

    # "said" / "quote" in the query biases DuckDuckGo toward pages that carry actual spoken
    # lines rather than biographical prose about the founder.
    query = f'"{name}" {theme} said quote interview'
    print(f"[insta-quotes] search: {query}", file=sys.stderr)

    try:
        results = _search(query)
    except httpx.HTTPError as e:
        print(f"[insta-quotes] search failed: {e}", file=sys.stderr)
        results = []

    sources: list[dict] = []
    seen_urls: set[str] = set()

    # Always try the founder's Wikiquote page directly. Wikiquote pages are pure quote
    # lists; DuckDuckGo doesn't always surface them for a themed query, but the URL is
    # predictable (First_Last with underscores). One extra request is cheap insurance
    # against a search that returns only third-party analysis pieces.
    wq_url = f"https://en.wikiquote.org/wiki/{urllib.parse.quote(name.replace(' ', '_'))}"
    wq_excerpts = _fetch_paragraphs(wq_url, must_contain=name)
    if wq_excerpts:
        sources.append({"title": f"{name} — Wikiquote", "url": wq_url,
                        "excerpts": wq_excerpts})
        seen_urls.add(wq_url)

    for r in results[:MAX_PAGES_TO_FETCH]:
        if r["url"] in seen_urls:
            continue
        paragraphs = _fetch_paragraphs(r["url"], must_contain=name)
        if paragraphs:
            sources.append(
                {
                    "title": r["title"],
                    "url": r["url"],
                    "excerpts": paragraphs,
                }
            )
            seen_urls.add(r["url"])
    # Include remaining search-result snippets as lighter-weight sources.
    for r in results[MAX_PAGES_TO_FETCH:]:
        if r["url"] in seen_urls:
            continue
        if r["snippet"]:
            sources.append(
                {"title": r["title"], "url": r["url"], "excerpts": [r["snippet"]]}
            )

    directive = (
        f"Using ONLY the excerpts below as grounding, write {count} "
        f"Instagram post{'s' if count != 1 else ''} in the voice of "
        f"{name} about {theme}. Each post must include:\n"
        "  - quote_text: 1-3 sentences, punchy, first-person, sounds like "
        f"{name} would say it. Do NOT fabricate specific numbers, dates, "
        "or events that aren't in the excerpts.\n"
        f"  - attribution: '{name}' plus a short qualifier (e.g. Founder, "
        "Zerodha).\n"
        "  - source_urls: the 1-2 excerpt URLs that most directly support "
        "the quote.\n"
        "  - instagram_caption: 2-4 short lines expanding on the quote, "
        "written for a founder/hustle audience.\n"
        "  - hashtags: 6-10 relevant tags, mixing broad "
        "(#entrepreneurship) and niche (#zerodha, #startupindia).\n"
        "  - image_prompt: one sentence describing a minimalist quote-card "
        "background image (color palette, mood, no text)."
    )

    return {
        "founder": {"name": name, "context_hint": context},
        "theme": theme,
        "count": count,
        "sources": sources,
        "synthesis_directive": directive,
        "output_schema": {
            "posts": [
                {
                    "quote_text": "string",
                    "attribution": "string",
                    "source_urls": ["string"],
                    "instagram_caption": "string",
                    "hashtags": ["string"],
                    "image_prompt": "string",
                }
            ]
        },
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
