"""Delivery for the apple-notes target.

Iterates the bundle. For each piece: check the ledger (skip if already
delivered), post to the bridge, write the outcome to the ledger. Never
raises — every piece's outcome is captured as a DeliveryRecord in the
report so the CLI can surface partial success cleanly.
"""
from __future__ import annotations

import sys

from delivery.bridge_client import BridgeClient, BridgeError
from delivery.ledger import Ledger
from delivery.models import (
    AppleNotesBundleIn,
    DeliveryRecord,
    DeliveryReport,
)


def deliver_apple_notes(
    bundle: AppleNotesBundleIn,
    ledger: Ledger,
    client: BridgeClient,
) -> DeliveryReport:
    records: list[DeliveryRecord] = []
    for piece in bundle.pieces:
        existing = ledger.get(bundle.run_id, piece.topic_id, "apple-notes")
        if existing is not None and existing.status == "delivered":
            records.append(ledger.record(
                bundle.run_id, piece.topic_id, "apple-notes",
                status="skipped",
                external_id=existing.external_id,
                meta={"reason": "already delivered", "prior_delivered_at": existing.delivered_at.isoformat() if existing.delivered_at else None},
            ))
            print(f"delivery: skip {piece.topic_id} — already delivered as {existing.external_id}", file=sys.stderr)
            continue

        try:
            data = client.create_note(name=piece.name, body_html=piece.body_html, folder=piece.folder)
        except BridgeError as e:
            records.append(ledger.record(
                bundle.run_id, piece.topic_id, "apple-notes",
                status="failed",
                external_id=None,
                meta={"error": str(e)},
            ))
            print(f"delivery: FAIL {piece.topic_id} — {e}", file=sys.stderr)
            continue

        records.append(ledger.record(
            bundle.run_id, piece.topic_id, "apple-notes",
            status="delivered",
            external_id=data["id"],
            meta={"note_name": data.get("name"), "note_folder": data.get("folder")},
        ))
        print(f"delivery: OK   {piece.topic_id} — created note {data['id']}", file=sys.stderr)

    return DeliveryReport(
        run_id=bundle.run_id,
        target="apple-notes",
        dry_run=False,
        records=records,
    )


def dry_run_apple_notes(bundle: AppleNotesBundleIn) -> DeliveryReport:
    """No bridge calls, no ledger writes. Emits a plausible report so pipeline wiring can be tested."""
    records = [
        DeliveryRecord(
            run_id=bundle.run_id,
            topic_id=piece.topic_id,
            target="apple-notes",
            status="dry-run",
            external_id=None,
            reason="dry-run: bridge not called, ledger not written",
            meta={
                "would_create": {
                    "name": piece.name,
                    "folder": piece.folder,
                    "body_html_len": len(piece.body_html),
                }
            },
        )
        for piece in bundle.pieces
    ]
    return DeliveryReport(run_id=bundle.run_id, target="apple-notes", dry_run=True, records=records)
