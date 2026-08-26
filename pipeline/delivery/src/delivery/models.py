"""Data contract for the delivery stage.

Input: subset mirrors of the renderer's target-specific bundles. Only
the fields we actually push are re-declared; extras are ignored so the
renderer can grow fields without breaking us. Source of truth is
`pipeline/renderer/src/renderer/models.py`.

Output: a `DeliveryReport` — one `DeliveryRecord` per piece the CLI
tried to deliver, describing what happened. Records for skipped pieces
carry `status="skipped"` with a reason (usually: already delivered).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_lax = ConfigDict(extra="ignore")

DeliveryTarget = Literal["apple-notes"]  # add "notion" when its transport lands
DeliveryStatus = Literal["delivered", "skipped", "failed", "dry-run"]


# --- Input: subset of renderer's AppleNotesBundle -----------------------

class AppleNotesPieceIn(BaseModel):
    model_config = _lax
    topic_id: str
    name: str
    body_html: str
    folder: str | None = None


class AppleNotesBundleIn(BaseModel):
    model_config = _lax
    run_id: str
    target: Literal["apple-notes"]
    pieces: list[AppleNotesPieceIn]


# --- Output: delivery report --------------------------------------------

class DeliveryRecord(BaseModel):
    """One piece's delivery outcome. Also what a ledger row deserializes into."""

    run_id: str
    topic_id: str
    target: DeliveryTarget
    status: DeliveryStatus
    external_id: str | None = Field(
        default=None,
        description="Downstream id (apple-notes note id, Notion page id). None on failure/skipped without id.",
    )
    delivered_at: datetime | None = Field(default=None, description="Set when status=delivered.")
    reason: str | None = Field(default=None, description="Filled on skipped/failed to explain why.")
    meta: dict[str, Any] = Field(default_factory=dict, description="Room for target-specific extras.")


class DeliveryReport(BaseModel):
    """Top-level output: one report per delivery run, one record per piece attempted."""

    run_id: str
    target: DeliveryTarget
    dry_run: bool
    records: list[DeliveryRecord]

    @property
    def counts(self) -> dict[str, int]:
        out = {"delivered": 0, "skipped": 0, "failed": 0, "dry-run": 0}
        for r in self.records:
            out[r.status] = out.get(r.status, 0) + 1
        return out
