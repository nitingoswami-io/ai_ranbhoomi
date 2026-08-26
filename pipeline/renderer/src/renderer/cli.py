"""CLI plumbing: read DraftBundle JSON, write a target-specific bundle JSON.

Unix-style stage — reads from stdin (or `--input path`), writes to stdout
(or `--output path`). Composes as:

    python -m writer -i trending.json | python -m renderer --target notion

Pick exactly one target per invocation. The output shape is
target-shaped (MarkdownBundle | AppleNotesBundle | NotionBundle) so
downstream delivery code doesn't have to unwrap a union.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from renderer.html_out import render_apple_notes
from renderer.markdown import render_markdown
from renderer.models import (
    AppleNotesBundle,
    DraftBundleIn,
    MarkdownBundle,
    MarkdownPiece,
    NotionBundle,
)
from renderer.notion_out import render_notion


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="pipeline-renderer",
        description="Turn writer DraftBundle JSON into a target-specific bundle.",
    )
    p.add_argument(
        "--target", "-t", required=True,
        choices=["markdown", "apple-notes", "notion"],
        help="Which format to render for. Determines the output bundle shape.",
    )
    p.add_argument(
        "--input", "-i", type=Path, default=None,
        help="Path to DraftBundle JSON. Default: read from stdin.",
    )
    p.add_argument(
        "--output", "-o", type=Path, default=None,
        help="Path to write the rendered bundle. Default: write to stdout.",
    )
    p.add_argument(
        "--apple-notes-folder", default=None,
        help="For --target apple-notes only: passed through as the `folder` on every piece.",
    )
    return p.parse_args(argv)


def _read_input(path: Path | None) -> DraftBundleIn:
    text = path.read_text() if path else sys.stdin.read()
    return DraftBundleIn.model_validate_json(text)


def _write_output(payload: str, path: Path | None) -> None:
    if path:
        path.write_text(payload)
    else:
        sys.stdout.write(payload + "\n")


def _build_bundle(bundle: DraftBundleIn, args: argparse.Namespace):  # type: ignore[no-untyped-def]
    if args.target == "markdown":
        return MarkdownBundle(
            run_id=bundle.run_id,
            pieces=[
                MarkdownPiece(topic_id=d.topic_id, title=d.headline, body=render_markdown(d))
                for d in bundle.drafts
            ],
        )
    if args.target == "apple-notes":
        return AppleNotesBundle(
            run_id=bundle.run_id,
            pieces=[render_apple_notes(d, folder=args.apple_notes_folder) for d in bundle.drafts],
        )
    if args.target == "notion":
        return NotionBundle(run_id=bundle.run_id, pieces=[render_notion(d) for d in bundle.drafts])
    raise ValueError(f"unknown target: {args.target}")  # argparse should have caught this


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    bundle = _read_input(args.input)

    if not bundle.drafts:
        print("renderer: input has no drafts", file=sys.stderr)

    out = _build_bundle(bundle, args)
    _write_output(out.model_dump_json(indent=2), args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
