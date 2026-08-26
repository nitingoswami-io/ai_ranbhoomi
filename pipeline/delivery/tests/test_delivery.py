"""Scaffold tests — models, ledger, dry-run, delivery against a mocked bridge.

Real bridge calls need macOS + a running notes-bridge; those aren't part
of the scaffold suite. The BridgeClient itself is exercised through
respx mocks, which is enough to cover the wire protocol.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from delivery.apple_notes import deliver_apple_notes, dry_run_apple_notes
from delivery.bridge_client import BridgeClient, BridgeError
from delivery.cli import main
from delivery.ledger import Ledger
from delivery.models import AppleNotesBundleIn, DeliveryReport

FIXTURE = Path(__file__).parent / "fixtures" / "apple_notes_bundle_sample.json"


def _load() -> AppleNotesBundleIn:
    return AppleNotesBundleIn.model_validate_json(FIXTURE.read_text())


# --- Models --------------------------------------------------------------

def test_bundle_parses():
    b = _load()
    assert b.target == "apple-notes"
    assert b.run_id == "test-run-0001"
    assert len(b.pieces) == 2


# --- Ledger --------------------------------------------------------------

def test_ledger_records_and_reads(tmp_path):
    with Ledger(tmp_path / "d.db") as led:
        assert led.get("run1", "t1", "apple-notes") is None
        rec = led.record("run1", "t1", "apple-notes", "delivered", external_id="notes-xyz")
        assert rec.status == "delivered"
        assert rec.external_id == "notes-xyz"
        assert rec.delivered_at is not None

        readback = led.get("run1", "t1", "apple-notes")
        assert readback is not None
        assert readback.external_id == "notes-xyz"


def test_ledger_upserts_on_same_key(tmp_path):
    with Ledger(tmp_path / "d.db") as led:
        led.record("run1", "t1", "apple-notes", "failed", meta={"error": "boom"})
        led.record("run1", "t1", "apple-notes", "delivered", external_id="notes-1")
        rec = led.get("run1", "t1", "apple-notes")
        assert rec.status == "delivered"
        assert rec.external_id == "notes-1"


# --- Dry-run -------------------------------------------------------------

def test_dry_run_no_side_effects(tmp_path):
    b = _load()
    report = dry_run_apple_notes(b)
    assert report.dry_run is True
    assert report.target == "apple-notes"
    assert len(report.records) == 2
    assert all(r.status == "dry-run" for r in report.records)
    assert all("would_create" in r.meta for r in report.records)


def test_cli_dry_run(tmp_path):
    out = tmp_path / "report.json"
    rc = main([
        "-i", str(FIXTURE),
        "-o", str(out),
        "--dry-run",
        "--ledger", str(tmp_path / "unused.db"),
    ])
    assert rc == 0
    report = DeliveryReport.model_validate_json(out.read_text())
    assert report.dry_run is True
    assert report.counts["dry-run"] == 2


# --- Delivery against mocked bridge --------------------------------------

@respx.mock
def test_delivery_happy_path(tmp_path):
    respx.post("http://fake-bridge/call").mock(side_effect=[
        httpx.Response(200, json={"ok": True, "data": {"id": "notes-1", "name": "n1", "folder": "Daily Brief"}}),
        httpx.Response(200, json={"ok": True, "data": {"id": "notes-2", "name": "n2", "folder": "Daily Brief"}}),
    ])

    b = _load()
    with Ledger(tmp_path / "d.db") as led, BridgeClient(url="http://fake-bridge", secret="s") as bc:
        report = deliver_apple_notes(b, led, bc)

    assert report.counts["delivered"] == 2
    ext_ids = [r.external_id for r in report.records]
    assert ext_ids == ["notes-1", "notes-2"]


@respx.mock
def test_delivery_is_idempotent(tmp_path):
    respx.post("http://fake-bridge/call").mock(return_value=httpx.Response(
        200, json={"ok": True, "data": {"id": "notes-1", "name": "n1", "folder": "Daily Brief"}},
    ))

    b = _load()
    b.pieces = b.pieces[:1]  # one piece for clarity

    with Ledger(tmp_path / "d.db") as led:
        with BridgeClient(url="http://fake-bridge", secret="s") as bc:
            first = deliver_apple_notes(b, led, bc)
        assert first.counts["delivered"] == 1

        with BridgeClient(url="http://fake-bridge", secret="s") as bc:
            second = deliver_apple_notes(b, led, bc)
        assert second.counts["skipped"] == 1
        assert second.records[0].external_id == "notes-1"


@respx.mock
def test_delivery_records_bridge_failure(tmp_path):
    respx.post("http://fake-bridge/call").mock(
        return_value=httpx.Response(500, json={"ok": False, "error": "osascript: folder not found"}),
    )

    b = _load()
    b.pieces = b.pieces[:1]

    with Ledger(tmp_path / "d.db") as led, BridgeClient(url="http://fake-bridge", secret="s") as bc:
        report = deliver_apple_notes(b, led, bc)

    assert report.counts["failed"] == 1
    assert "folder not found" in report.records[0].meta["error"]


@respx.mock
def test_bridge_client_raises_on_401(tmp_path):
    respx.post("http://fake-bridge/call").mock(
        return_value=httpx.Response(401, json={"ok": False, "error": "unauthorized"}),
    )

    with BridgeClient(url="http://fake-bridge", secret="wrong") as bc:
        with pytest.raises(BridgeError, match="unauthorized"):
            bc.create_note(name="x", body_html="<p>x</p>")
