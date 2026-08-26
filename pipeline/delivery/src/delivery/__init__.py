"""Pipeline stage 4 — delivery.

Reads a target-specific bundle from the renderer and pushes each piece
to its downstream. Today: apple-notes only (via the notes-bridge HTTP
endpoint on localhost:48213). Notion delivery and the mark_covered
loop-close to trend-radar are next.

Idempotent re-runs are guaranteed by a local SQLite ledger keyed on
(run_id, topic_id, target). Re-running the same bundle skips pieces
that were already delivered — nothing hits Apple Notes twice.
"""
__version__ = "0.1.0"
