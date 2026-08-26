#!/usr/bin/env bash
# Installs notes-bridge as a per-user LaunchAgent.
# Generates a shared secret, wires it into both the plist and a `.secret`
# file that you feed to `docker mcp secret set`.
#
# Idempotent — safe to re-run; the existing plist is unloaded first.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.local.notes-bridge"
PLIST_TEMPLATE="$SCRIPT_DIR/${LABEL}.plist.template"
PLIST_DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"
BRIDGE_PY="$SCRIPT_DIR/bridge.py"
SECRET_FILE="$SCRIPT_DIR/.secret"

if [[ ! -f "$BRIDGE_PY" ]]; then
    echo "error: $BRIDGE_PY not found" >&2
    exit 1
fi
if [[ ! -f "$PLIST_TEMPLATE" ]]; then
    echo "error: $PLIST_TEMPLATE not found" >&2
    exit 1
fi

# Prefer python3 on PATH; fall back to /usr/bin/python3 which ships with
# macOS Command Line Tools.
PYTHON_BIN="$(command -v python3 || true)"
if [[ -z "$PYTHON_BIN" ]]; then
    PYTHON_BIN="/usr/bin/python3"
fi
if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "error: no python3 found; install with 'xcode-select --install'" >&2
    exit 1
fi

# Reuse existing secret if present so re-runs don't invalidate the
# docker-mcp secret you've already registered.
if [[ -f "$SECRET_FILE" ]]; then
    SECRET="$(cat "$SECRET_FILE")"
    echo "reusing existing secret from $SECRET_FILE"
else
    SECRET="$("$PYTHON_BIN" -c 'import secrets; print(secrets.token_urlsafe(32))')"
    umask 077
    printf '%s' "$SECRET" > "$SECRET_FILE"
    echo "wrote new secret to $SECRET_FILE (mode 600)"
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"

# Escape the substitution values for sed's replacement side. In practice
# these paths won't contain '&' or '|', but paranoid escaping is cheap.
esc() { printf '%s' "$1" | sed -e 's/[\/&|]/\\&/g'; }

sed \
    -e "s|__PYTHON__|$(esc "$PYTHON_BIN")|g" \
    -e "s|__BRIDGE_PY__|$(esc "$BRIDGE_PY")|g" \
    -e "s|__HOME__|$(esc "$HOME")|g" \
    -e "s|__SECRET__|$(esc "$SECRET")|g" \
    "$PLIST_TEMPLATE" > "$PLIST_DEST"
chmod 600 "$PLIST_DEST"
echo "wrote $PLIST_DEST"

# `bootout` fails harmlessly if the service isn't loaded; ignore its exit code.
launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_DEST"
launchctl kickstart -k "gui/$(id -u)/${LABEL}"

echo ""
echo "notes-bridge installed and started."
echo ""
echo "Register the secret with Docker MCP Toolkit:"
echo "    docker mcp secret set apple-notes.bridge-secret \"\$(cat $SECRET_FILE)\""
echo ""
echo "Probe it:"
echo "    curl -s http://127.0.0.1:48213/health"
echo ""
echo "Logs:"
echo "    tail -f $HOME/Library/Logs/notes-bridge.err"
