"""CLI plumbing for delivery.

Reads a target-specific bundle from the renderer (stdin or --input),
delivers each piece, writes a DeliveryReport (stdout or --output).

Today: apple-notes only. When Notion delivery is added, `--target` will
dispatch here on the bundle's target field.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from delivery.apple_notes import deliver_apple_notes, dry_run_apple_notes
from delivery.bridge_client import DEFAULT_URL, BridgeClient
from delivery.ledger import Ledger
from delivery.models import AppleNotesBundleIn, DeliveryReport

DEFAULT_LEDGER = Path(os.environ.get("DELIVERY_LEDGER", "./data/delivery.db"))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="pipeline-delivery",
        description="Deliver a rendered bundle to its target. Idempotent via local SQLite ledger.",
    )
    p.add_argument(
        "--input", "-i", type=Path, default=None,
        help="Path to a rendered bundle JSON. Default: read from stdin.",
    )
    p.add_argument(
        "--output", "-o", type=Path, default=None,
        help="Path to write the DeliveryReport JSON. Default: write to stdout.",
    )
    p.add_argument(
        "--ledger", type=Path, default=DEFAULT_LEDGER,
        help=f"SQLite ledger path. Default: {DEFAULT_LEDGER} (env DELIVERY_LEDGER).",
    )
    p.add_argument(
        "--bridge-url", default=os.environ.get("NOTES_BRIDGE_URL", DEFAULT_URL),
        help=f"apple-notes bridge URL. Default: {DEFAULT_URL} (env NOTES_BRIDGE_URL).",
    )
    p.add_argument(
        "--bridge-secret", default=os.environ.get("NOTES_BRIDGE_SECRET"),
        help="apple-notes bridge shared secret. Default: env NOTES_BRIDGE_SECRET.",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Do not hit the bridge, do not write to the ledger. Report what WOULD happen.",
    )
    return p.parse_args(argv)


def _read_input(path: Path | None) -> AppleNotesBundleIn:
    text = path.read_text() if path else sys.stdin.read()
    return AppleNotesBundleIn.model_validate_json(text)


def _write_output(report: DeliveryReport, path: Path | None) -> None:
    payload = report.model_dump_json(indent=2)
    if path:
        path.write_text(payload)
    else:
        sys.stdout.write(payload + "\n")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    bundle = _read_input(args.input)

    if bundle.target != "apple-notes":
        print(f"delivery: unsupported target {bundle.target!r} — only 'apple-notes' is wired today",
              file=sys.stderr)
        return 2

    if args.dry_run:
        report = dry_run_apple_notes(bundle)
    else:
        if not args.bridge_secret:
            print(
                "delivery: NOTES_BRIDGE_SECRET is empty. The bridge should reject unauthenticated "
                "requests; if it's running with auth disabled, pass --bridge-secret '' to acknowledge.",
                file=sys.stderr,
            )
            # Continue — bridge decides. Empty secret triggers the client to skip the header,
            # which the bridge treats as unauth and rejects with 401 (unless bridge SECRET is also empty).

        with Ledger(args.ledger) as ledger, \
             BridgeClient(url=args.bridge_url, secret=args.bridge_secret) as client:
            report = deliver_apple_notes(bundle, ledger, client)

    _write_output(report, args.output)

    counts = report.counts
    print(
        f"delivery: {counts.get('delivered', 0)} delivered, "
        f"{counts.get('skipped', 0)} skipped, "
        f"{counts.get('failed', 0)} failed, "
        f"{counts.get('dry-run', 0)} dry-run",
        file=sys.stderr,
    )

    return 1 if counts.get("failed", 0) else 0


if __name__ == "__main__":
    sys.exit(main())
