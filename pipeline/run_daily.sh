#!/usr/bin/env bash
# Daily orchestrator — fetch → writer → renderer → delivery.
#
# Meant to be invoked by launchd (see pipeline/launchd/) or manually. All
# stages write to $LOG_DIR/<timestamp>.log. Sources $REPO/pipeline/.env
# if present so ANTHROPIC_API_KEY and friends don't need to be set in
# launchd's minimal environment.
#
# Env overrides (put in pipeline/.env or export before invoking):
#   ANTHROPIC_API_KEY      required — writer's LLM
#   NOTES_BRIDGE_SECRET    apple-notes bridge secret; if empty, read from bridge/.secret
#   TREND_RADAR_DB         trend-radar ledger; defaults to pipeline/data/trend_radar.db
#   LOOKBACK_HOURS         trend-radar window (default 24)
#   TOPIC_LIMIT            max non-suppressed topics to write (default 5)
#   APPLE_NOTES_FOLDER     target folder in Notes (default "Daily Brief")
#   WRITER_MODEL           override writer's LLM (default anthropic:claude-sonnet-4-6)
#   DRY_RUN=1              pass --dry-run through writer and delivery

set -eo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PIPELINE="$REPO/pipeline"
LOG_DIR="$PIPELINE/logs"
DATA_DIR="$PIPELINE/data"
mkdir -p "$LOG_DIR" "$DATA_DIR"

TS="$(date '+%Y-%m-%d_%H%M%S')"
LOG="$LOG_DIR/$TS.log"

# Load env before anything else — API keys, bridge secret, overrides.
if [ -f "$PIPELINE/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$PIPELINE/.env"
    set +a
fi

# Convenience: if NOTES_BRIDGE_SECRET wasn't in .env, pull it from the bridge
# install location. Same file the LaunchAgent reads.
if [ -z "${NOTES_BRIDGE_SECRET:-}" ]; then
    SECRET_FILE="$REPO/mcp_server/apple-notes/bridge/.secret"
    if [ -f "$SECRET_FILE" ]; then
        NOTES_BRIDGE_SECRET="$(cat "$SECRET_FILE")"
        export NOTES_BRIDGE_SECRET
    fi
fi

export TREND_RADAR_DB="${TREND_RADAR_DB:-$DATA_DIR/trend_radar.db}"

TREND_PY="$REPO/mcp_server/trend-radar-mcp/.venv/bin/python"
WRITER_PY="$PIPELINE/writer/.venv/bin/python"
RENDERER_PY="$PIPELINE/renderer/.venv/bin/python"
DELIVERY_PY="$PIPELINE/delivery/.venv/bin/python"

for py in "$TREND_PY" "$WRITER_PY" "$RENDERER_PY" "$DELIVERY_PY"; do
    if [ ! -x "$py" ]; then
        echo "run_daily: missing $py — run 'uv sync' in the owning package first" >&2
        exit 2
    fi
done

FETCH_SCRIPT="$REPO/mcp_server/trend-radar-mcp/scripts/fetch.py"
MARK_COVERED_SCRIPT="$REPO/mcp_server/trend-radar-mcp/scripts/mark_covered.py"
LOOKBACK="${LOOKBACK_HOURS:-24}"
LIMIT="${TOPIC_LIMIT:-5}"
FOLDER="${APPLE_NOTES_FOLDER:-Daily Brief}"

WRITER_ARGS=()
DELIVERY_ARGS=()
if [ "${DRY_RUN:-0}" = "1" ]; then
    WRITER_ARGS+=(--dry-run)
    DELIVERY_ARGS+=(--dry-run)
fi

{
    echo "=== $(date -Iseconds) ai_ranbhoomi daily run ==="
    echo "repo:        $REPO"
    echo "lookback:    ${LOOKBACK}h"
    echo "limit:       $LIMIT topics"
    echo "folder:      $FOLDER"
    echo "dry_run:     ${DRY_RUN:-0}"
    echo "trend db:    $TREND_RADAR_DB"
    echo

    "$TREND_PY" "$FETCH_SCRIPT" --lookback-hours "$LOOKBACK" --limit "$LIMIT" \
      | "$WRITER_PY" -m writer "${WRITER_ARGS[@]}" \
      | "$RENDERER_PY" -m renderer -t apple-notes --apple-notes-folder "$FOLDER" \
      | "$DELIVERY_PY" -m delivery "${DELIVERY_ARGS[@]}" \
      | "$TREND_PY" "$MARK_COVERED_SCRIPT"

    echo
    echo "=== $(date -Iseconds) done ==="
} 2>&1 | tee "$LOG"
