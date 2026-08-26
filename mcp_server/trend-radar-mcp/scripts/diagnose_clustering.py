"""Diagnose why LLM clustering keeps hitting the retry limit.

Fetches a fresh batch of items from HN only (fast — no arXiv/HF), then
calls the clustering agent with `capture_run_messages` so we can see the
model's raw output and every retry reason before the outer exception
swallows them.

Runs three scenarios:
  1. Small batch (20 items) — proves the pipeline works when output is bounded.
  2. Full batch — reproduces the failure and shows the specific ModelRetry.
  3. Full batch with max_tokens raised — tests the "output truncation" hypothesis.

Prints all validator retry messages the model saw. This is what the
production `except Exception as exc` is hiding.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from pydantic_ai import Agent, capture_run_messages

from trend_radar.adapters import build_adapters
from trend_radar.clustering import _build_agent, _format_prompt, _item_key
from trend_radar.config import get_settings
from trend_radar.http import create_http_client
from trend_radar.models import RawItem


def _summarize_messages(messages: list[Any]) -> None:
    """Walk the message log and surface retry reasons + raw model output."""
    for i, msg in enumerate(messages):
        kind = type(msg).__name__
        # Try to peel out the parts we care about without importing pydantic-ai internals.
        parts = getattr(msg, "parts", None)
        if not parts:
            print(f"  [{i}] {kind}")
            continue
        for j, part in enumerate(parts):
            part_kind = type(part).__name__
            if part_kind == "RetryPromptPart":
                content = getattr(part, "content", "")
                if isinstance(content, list):
                    for c in content:
                        msg_text = c.get("msg", "") if isinstance(c, dict) else str(c)
                        print(f"  [{i}.{j}] RETRY: {msg_text[:300]}")
                else:
                    print(f"  [{i}.{j}] RETRY: {str(content)[:300]}")
            elif part_kind == "TextPart":
                text = getattr(part, "content", "")
                print(f"  [{i}.{j}] MODEL TEXT ({len(text)} chars): {text[:200]}...")
            elif part_kind == "ToolCallPart":
                tool = getattr(part, "tool_name", "?")
                args = getattr(part, "args", "")
                args_str = str(args)
                print(f"  [{i}.{j}] TOOL_CALL {tool} ({len(args_str)} chars args): {args_str[:200]}...")
            else:
                print(f"  [{i}.{j}] {part_kind}")


async def _try_clustering(
    items: list[RawItem],
    model: str,
    *,
    max_tokens: int | None = None,
    label: str,
) -> bool:
    print(f"\n=== {label} ===")
    print(f"items: {len(items)}   model: {model}   max_tokens: {max_tokens or 'default'}")

    agent: Agent = _build_agent(model)
    if max_tokens is not None:
        # Pydantic-ai reads model_settings on each run; setting the attribute
        # after construction is supported (see Agent.__init__ signature).
        agent.model_settings = {"max_tokens": max_tokens}

    all_ids = {_item_key(i) for i in items}
    prompt = _format_prompt(items)
    print(f"prompt: {len(prompt)} chars ({sum(1 for _ in prompt.splitlines())} lines)")

    with capture_run_messages() as messages:
        try:
            result = await agent.run(prompt, deps=all_ids)
            n_clusters = len(result.output)
            n_assigned = sum(len(c.item_ids) for c in result.output)
            print(f"SUCCESS: {n_clusters} clusters, {n_assigned}/{len(items)} items assigned")
            return True
        except Exception as e:
            print(f"FAILED ({type(e).__name__}): {e}")
            print("--- captured message trail ---")
            _summarize_messages(messages)
            return False


async def main(limit: int) -> int:
    settings = get_settings()
    if not settings.has_anthropic_key():
        print("no ANTHROPIC_API_KEY set — this diagnostic exists only for the LLM path")
        return 2
    print(f"model: {settings.llm_model}")

    async with create_http_client() as client:
        adapters = build_adapters(settings, client)
        all_items: list[RawItem] = []
        for a in adapters:
            got = await a.fetch(24)
            print(f"  {a.__class__.__name__}: {len(got)} items")
            all_items.extend(got)
    items = all_items
    print(f"fetched {len(items)} items total (matches production)")

    if limit and limit < len(items):
        items = items[:limit]
        print(f"truncated to {len(items)}")

    # Two hypotheses to prove:
    #   H1: default max_tokens truncates the tool-call output past ~40 items.
    #   H2: setting max_tokens=16000 unblocks it.
    for n in (50, 140):
        if n > len(items):
            continue
        await _try_clustering(items[:n], settings.llm_model,
                              label=f"{n} items, default max_tokens")
        await _try_clustering(items[:n], settings.llm_model, max_tokens=16000,
                              label=f"{n} items, max_tokens=16000")

    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Diagnose LLM clustering failures.")
    ap.add_argument("--limit", type=int, default=0, help="Cap fetched items (0 = no cap)")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(args.limit)))
