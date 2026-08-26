"""A minimal MCP server: deterministic dice rolling.

Deliberately dependency-light so the container stays small and the
protocol mechanics stay visible.

SDK NOTE: this targets mcp >= 2.0, where the server class is
`mcp.server.mcpserver.MCPServer`. Most tutorials online still show
`from mcp.server.fastmcp import FastMCP` — that path was removed in 2.0.
If you're pinned to 1.x, swap the import and use `FastMCP(...)`; the
decorator and run() signatures are otherwise the same.

STDIO RULE: stdout belongs to the JSON-RPC transport. Anything you print
there corrupts the protocol stream and the gateway drops the server with
an opaque parse error. All diagnostics go to stderr.
"""

import random
import re
import sys

from mcp.server.mcpserver import MCPServer

# The name here is what shows up in tool namespacing on the client side.
mcp = MCPServer(name="dice", version="0.1.0")

NOTATION = re.compile(r"^(\d{1,3})d(\d{1,3})$")


@mcp.tool()
def roll(notation: str = "4d6", drop_lowest: bool = False, seed: int | None = None) -> dict:
    """Roll dice in NdM notation and return the individual dice.

    Args:
        notation: Dice expression such as "4d6" or "2d20".
        drop_lowest: If true, discard the single lowest die before summing.
        seed: Optional integer seed. Supplying one makes the roll fully
            reproducible - same seed, same dice, every time.

    Returns:
        The dice rolled, the die dropped (if any), the total, and the seed
        actually used.
    """
    match = NOTATION.match(notation.strip().lower())
    if not match:
        raise ValueError(f"Bad notation {notation!r}; expected something like '4d6'.")

    count, sides = int(match.group(1)), int(match.group(2))
    if count < 1 or sides < 2:
        raise ValueError("Need at least 1 die with at least 2 sides.")

    # If the caller didn't pick a seed, generate and report one so the
    # roll stays auditable after the fact.
    used_seed = seed if seed is not None else random.SystemRandom().randrange(2**32)
    rng = random.Random(used_seed)

    dice = sorted(rng.randint(1, sides) for _ in range(count))
    dropped = dice[0] if (drop_lowest and count > 1) else None
    kept = dice[1:] if dropped is not None else dice

    print(f"[dice] rolled {notation} seed={used_seed}", file=sys.stderr)

    return {
        "notation": notation,
        "dice": dice,
        "dropped": dropped,
        "kept": kept,
        "total": sum(kept),
        "seed": used_seed,
    }


@mcp.tool()
def stat_block(seed: int | None = None) -> dict:
    """Generate a six-score D&D ability array: 4d6 drop lowest, six times."""
    used_seed = seed if seed is not None else random.SystemRandom().randrange(2**32)
    rng = random.Random(used_seed)

    rolls = []
    for _ in range(6):
        dice = sorted(rng.randint(1, 6) for _ in range(4))
        rolls.append({"dice": dice, "dropped": dice[0], "score": sum(dice[1:])})

    scores = sorted((r["score"] for r in rolls), reverse=True)
    return {"rolls": rolls, "scores": scores, "total": sum(scores), "seed": used_seed}


if __name__ == "__main__":
    # stdio is what the Docker MCP Gateway attaches to.
    mcp.run(transport="stdio")
