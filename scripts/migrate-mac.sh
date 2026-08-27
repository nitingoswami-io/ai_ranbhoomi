#!/usr/bin/env bash
# migrate-mac.sh — set up ai_ranbhoomi on a fresh Mac.
#
# Idempotent — safe to re-run. Each section checks state before acting.
#
# What it does automatically:
#   1.  Verify prerequisites (git, uv; installs uv if missing)
#   2.  uv sync every package (4 mcp_server/*, 3 pipeline/*)
#   3.  Write pipeline/.env (prompts for ANTHROPIC_API_KEY unless present)
#   4.  Install/refresh the apple-notes LaunchAgent via install-bridge.sh
#   5.  Install the daily-runner LaunchAgent (fires at 21:00 daily)
#   6.  Run install-bridge.sh --doctor + a dry-run of the daily pipeline
#
# What you still have to do by hand (see scripts/MIGRATION.md):
#   - Grant the bridge's python Automation access to Notes (System Settings)
#   - Create a "Daily Brief" folder in Notes.app
#   - On a headless Mac mini: enable auto-login so a user session exists at
#     boot (Notes.app + osascript need one)
#
# Usage:
#     scripts/migrate-mac.sh              # full install/repair
#     scripts/migrate-mac.sh --doctor     # validate only, touch nothing
#     scripts/migrate-mac.sh --skip-bridge # e.g. bridge is fine, skip re-install
#     scripts/migrate-mac.sh --skip-launchd
#
# Env overrides (useful for CI/automation):
#     ANTHROPIC_API_KEY=sk-...           bypasses the prompt
#     APPLE_NOTES_FOLDER="Daily Brief"   default for pipeline/.env
#     TOPIC_LIMIT=5                      default for pipeline/.env

set -euo pipefail

# -- Args ----------------------------------------------------------------

DOCTOR=0
SKIP_BRIDGE=0
SKIP_LAUNCHD=0
for arg in "$@"; do
    case "$arg" in
        --doctor)       DOCTOR=1 ;;
        --skip-bridge)  SKIP_BRIDGE=1 ;;
        --skip-launchd) SKIP_LAUNCHD=1 ;;
        -h|--help)      sed -n '/^# /,/^$/p' "$0" | sed 's/^# //'; exit 0 ;;
        *) echo "unknown arg: $arg" >&2; exit 2 ;;
    esac
done

# -- Paths ---------------------------------------------------------------

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIPELINE="$REPO/pipeline"
LOG_DIR="$PIPELINE/logs"
DATA_DIR="$PIPELINE/data"

BRIDGE_SCRIPT="$REPO/mcp_server/apple-notes/bridge/install-bridge.sh"
LAUNCHD_TEMPLATE="$PIPELINE/launchd/com.nitin.ai-ranbhoomi.daily.plist.template"
LAUNCHD_LABEL="com.nitin.ai-ranbhoomi.daily"
LAUNCHD_DEST="$HOME/Library/LaunchAgents/${LAUNCHD_LABEL}.plist"

UV_PACKAGES=(
    "mcp_server/trend-radar-mcp"
    "mcp_server/apple-notes/server"   # if present; skipped otherwise
    "mcp_server/insta-quotes"          # if present; skipped otherwise
    "mcp_server/dice"                  # if present; skipped otherwise
    "pipeline/writer"
    "pipeline/renderer"
    "pipeline/delivery"
)

# -- Helpers -------------------------------------------------------------

STEP=0
step() { STEP=$((STEP + 1)); echo; echo "==[ $STEP. $* ]=================================="; }
ok()   { echo "  ok:   $*"; }
info() { echo "  info: $*"; }
warn() { echo "  warn: $*" >&2; }
fail() { echo "  FAIL: $*" >&2; DOCTOR_FAIL=1; }

DOCTOR_FAIL=0

# -- 1. Prerequisites ----------------------------------------------------

step "prerequisites"

if [[ "$(uname)" != "Darwin" ]]; then
    warn "not macOS — the notes-bridge won't work here"
fi

if ! command -v git >/dev/null; then
    fail "git not found — install Xcode Command Line Tools: xcode-select --install"
fi

if ! command -v uv >/dev/null; then
    if [[ $DOCTOR -eq 1 ]]; then
        fail "uv not found"
    else
        info "uv not found — installing via astral installer"
        curl -LsSf https://astral.sh/uv/install.sh | sh
        # shellcheck disable=SC1091
        [[ -f "$HOME/.local/bin/env" ]] && . "$HOME/.local/bin/env"
        export PATH="$HOME/.local/bin:$PATH"
    fi
fi
command -v uv >/dev/null && ok "uv: $(uv --version)"
command -v git >/dev/null && ok "git: $(git --version | head -1)"

# -- 2. uv sync all packages --------------------------------------------

step "uv sync per package"

if [[ $DOCTOR -eq 1 ]]; then
    for pkg in "${UV_PACKAGES[@]}"; do
        if [[ -f "$REPO/$pkg/pyproject.toml" ]]; then
            if [[ -x "$REPO/$pkg/.venv/bin/python" ]]; then
                ok "$pkg — venv present"
            else
                fail "$pkg — no .venv (run migrate-mac.sh without --doctor to install)"
            fi
        fi
    done
else
    for pkg in "${UV_PACKAGES[@]}"; do
        if [[ ! -f "$REPO/$pkg/pyproject.toml" ]]; then
            info "$pkg — no pyproject.toml, skipping"
            continue
        fi
        echo "  sync: $pkg"
        # --extra dev where the pyproject declares one; harmless otherwise.
        if grep -q "\\[project.optional-dependencies\\]" "$REPO/$pkg/pyproject.toml" 2>/dev/null; then
            ( cd "$REPO/$pkg" && uv sync --extra dev >/dev/null )
        else
            ( cd "$REPO/$pkg" && uv sync >/dev/null )
        fi
        ok "$pkg synced"
    done
fi

# -- 3. pipeline/.env ----------------------------------------------------

step "pipeline/.env"

ENV_FILE="$PIPELINE/.env"
if [[ $DOCTOR -eq 1 ]]; then
    if [[ -f "$ENV_FILE" ]]; then
        if grep -q "^ANTHROPIC_API_KEY=" "$ENV_FILE" && \
           ! grep -q "^ANTHROPIC_API_KEY=sk-ant-\\.\\.\\." "$ENV_FILE" && \
           ! grep -q "^ANTHROPIC_API_KEY=$" "$ENV_FILE"; then
            ok ".env exists and ANTHROPIC_API_KEY is set"
        else
            fail ".env exists but ANTHROPIC_API_KEY looks unset"
        fi
    else
        fail ".env not found — run migrate-mac.sh without --doctor to create it"
    fi
else
    if [[ ! -f "$ENV_FILE" ]]; then
        cp "$PIPELINE/.env.example" "$ENV_FILE"
        ok "copied .env.example → .env"
    else
        ok ".env already present, leaving as-is"
    fi

    if grep -q "^ANTHROPIC_API_KEY=sk-ant-\\.\\.\\." "$ENV_FILE" || \
       grep -q "^ANTHROPIC_API_KEY=$" "$ENV_FILE"; then
        if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
            sed -i.bak "s|^ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}|" "$ENV_FILE"
            ok "wrote ANTHROPIC_API_KEY from environment"
        else
            echo
            printf "Enter your ANTHROPIC_API_KEY (starts with sk-ant-): "
            read -r ANTHROPIC_API_KEY
            if [[ -n "$ANTHROPIC_API_KEY" ]]; then
                sed -i.bak "s|^ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}|" "$ENV_FILE"
                ok "wrote ANTHROPIC_API_KEY to .env"
            else
                warn "no key entered — the pipeline will fail at the writer stage. Edit $ENV_FILE later."
            fi
        fi
    else
        ok "ANTHROPIC_API_KEY already set"
    fi

    # Fill in APPLE_NOTES_FOLDER + TOPIC_LIMIT if provided via env
    if [[ -n "${APPLE_NOTES_FOLDER:-}" ]]; then
        # Add if missing, replace if commented
        if grep -q "^APPLE_NOTES_FOLDER=" "$ENV_FILE"; then
            sed -i.bak "s|^APPLE_NOTES_FOLDER=.*|APPLE_NOTES_FOLDER=\"$APPLE_NOTES_FOLDER\"|" "$ENV_FILE"
        else
            echo "APPLE_NOTES_FOLDER=\"$APPLE_NOTES_FOLDER\"" >> "$ENV_FILE"
        fi
        ok "APPLE_NOTES_FOLDER set to \"$APPLE_NOTES_FOLDER\""
    fi
    if [[ -n "${TOPIC_LIMIT:-}" ]]; then
        if grep -q "^TOPIC_LIMIT=" "$ENV_FILE"; then
            sed -i.bak "s|^TOPIC_LIMIT=.*|TOPIC_LIMIT=$TOPIC_LIMIT|" "$ENV_FILE"
        else
            echo "TOPIC_LIMIT=$TOPIC_LIMIT" >> "$ENV_FILE"
        fi
        ok "TOPIC_LIMIT set to $TOPIC_LIMIT"
    fi
    rm -f "${ENV_FILE}.bak"
fi

mkdir -p "$LOG_DIR" "$DATA_DIR"

# -- 4. apple-notes bridge ----------------------------------------------

step "apple-notes bridge"

if [[ $SKIP_BRIDGE -eq 1 ]]; then
    info "skipped (--skip-bridge)"
else
    if [[ $DOCTOR -eq 1 ]]; then
        "$BRIDGE_SCRIPT" --doctor || fail "bridge doctor reported problems"
    else
        "$BRIDGE_SCRIPT"
        ok "bridge installer ran; verifying"
        "$BRIDGE_SCRIPT" --doctor || warn "bridge doctor reported problems — check output above"
    fi
fi

# -- 5. daily-runner LaunchAgent ----------------------------------------

step "daily-runner LaunchAgent"

if [[ $SKIP_LAUNCHD -eq 1 ]]; then
    info "skipped (--skip-launchd)"
elif [[ $DOCTOR -eq 1 ]]; then
    if [[ -f "$LAUNCHD_DEST" ]]; then
        ok "plist installed: $LAUNCHD_DEST"
        if launchctl list | grep -q "$LAUNCHD_LABEL"; then
            ok "loaded in launchd"
        else
            fail "not loaded — run: launchctl bootstrap gui/\$(id -u) $LAUNCHD_DEST"
        fi
    else
        fail "plist not installed — run migrate-mac.sh without --doctor"
    fi
else
    mkdir -p "$(dirname "$LAUNCHD_DEST")"
    sed "s|__REPO__|$REPO|g" "$LAUNCHD_TEMPLATE" > "$LAUNCHD_DEST"
    ok "wrote $LAUNCHD_DEST"

    # Reload if already loaded, otherwise bootstrap.
    if launchctl list | grep -q "$LAUNCHD_LABEL"; then
        launchctl bootout "gui/$(id -u)/$LAUNCHD_LABEL" 2>/dev/null || true
    fi
    launchctl bootstrap "gui/$(id -u)" "$LAUNCHD_DEST"
    ok "loaded LaunchAgent — will fire at 21:00 daily"
fi

# -- 6. Final verification ----------------------------------------------

step "verify"

# Bridge health
if curl -sf http://localhost:48213/health >/dev/null 2>&1; then
    health="$(curl -s http://localhost:48213/health)"
    case "$health" in
        *reachable*not-reachable*)
            warn "bridge up but Notes unreachable — grant Automation permission (see MIGRATION.md)"
            ;;
        *reachable*)
            ok "bridge health: $health"
            ;;
    esac
else
    fail "bridge /health did not respond"
fi

# Check "Daily Brief" folder exists (best-effort)
if [[ -f "$REPO/mcp_server/apple-notes/bridge/.secret" ]]; then
    SECRET="$(cat "$REPO/mcp_server/apple-notes/bridge/.secret")"
    if folders="$(curl -s -X POST http://localhost:48213/call \
        -H "Content-Type: application/json" \
        -H "X-Bridge-Secret: $SECRET" \
        -d '{"action":"list_folders","params":{}}' 2>/dev/null)"; then
        target="${APPLE_NOTES_FOLDER:-Daily Brief}"
        if echo "$folders" | grep -q "\"name\": \"$target\""; then
            ok "\"$target\" folder present in Notes"
        else
            warn "\"$target\" folder NOT present — create it in Notes.app before the first real run"
        fi
    fi
fi

# LaunchAgent state
if launchctl list | grep -q "$LAUNCHD_LABEL"; then
    ok "daily-runner registered with launchd"
else
    warn "daily-runner not registered"
fi

echo
if [[ $DOCTOR_FAIL -eq 0 ]]; then
    echo "migration script done."
    if [[ $DOCTOR -eq 0 ]]; then
        echo
        echo "next: test a real run (creates real Notes):"
        echo "    $PIPELINE/run_daily.sh"
        echo
        echo "or a dry-run first (no LLM cost, no notes created):"
        echo "    DRY_RUN=1 $PIPELINE/run_daily.sh"
    fi
    exit 0
else
    echo "one or more checks failed — see FAIL lines above."
    exit 1
fi
