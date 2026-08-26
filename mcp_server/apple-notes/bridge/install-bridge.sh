#!/usr/bin/env bash
# Installs notes-bridge as a per-user LaunchAgent.
# Generates a shared secret, wires it into both the plist and a `.secret`
# file that you feed to `docker mcp secret set`.
#
# Idempotent — safe to re-run; the existing plist is unloaded first.
#
# Usage:
#     install-bridge.sh              # install/re-install
#     install-bridge.sh --doctor     # validate an existing install, don't touch anything

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.local.notes-bridge"
PLIST_TEMPLATE="$SCRIPT_DIR/${LABEL}.plist.template"
PLIST_DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"
BRIDGE_PY="$SCRIPT_DIR/bridge.py"
SECRET_FILE="$SCRIPT_DIR/.secret"

# --- doctor mode ---------------------------------------------------------
# Validates the currently-installed plist without touching anything.
# Catches exactly the failure modes we hit today: dangling interpreter,
# script moved out from under the plist, secret mismatch, unreachable
# Notes.app. Exit 0 = all green, 1 = at least one problem.
if [[ "${1:-}" == "--doctor" ]]; then
    fail=0
    say_ok()   { echo "  ok:   $*"; }
    say_fail() { echo "  FAIL: $*" >&2; fail=1; }

    echo "plist: $PLIST_DEST"
    if [[ ! -f "$PLIST_DEST" ]]; then
        say_fail "not installed — run install-bridge.sh (without --doctor)"
        exit 1
    fi
    say_ok "installed"

    installed_py="$(plutil -extract 'ProgramArguments.0' raw "$PLIST_DEST" 2>/dev/null || true)"
    installed_script="$(plutil -extract 'ProgramArguments.1' raw "$PLIST_DEST" 2>/dev/null || true)"
    installed_secret="$(plutil -extract 'EnvironmentVariables.NOTES_BRIDGE_SECRET' raw "$PLIST_DEST" 2>/dev/null || true)"

    echo "python: $installed_py"
    if [[ -x "$installed_py" ]]; then
        say_ok "interpreter exists and is executable"
        case "$installed_py" in
            /usr/bin/python3)
                say_fail "this is Xcode's TCC-sandboxed python — it cannot read files under ~/Documents/ from a LaunchAgent" ;;
            */anaconda*|*/miniconda*|*/miniforge*)
                echo "  warn: conda-managed python — will silently break if you remove conda" ;;
        esac
    else
        say_fail "interpreter is missing — the LaunchAgent will exit 78 (EX_CONFIG). Re-run install-bridge.sh."
    fi

    echo "script: $installed_script"
    if [[ -f "$installed_script" ]]; then
        say_ok "bridge.py exists at the path the plist points to"
        if [[ "$installed_script" != "$BRIDGE_PY" ]]; then
            echo "  warn: plist points at $installed_script but this checkout is at $BRIDGE_PY"
            echo "        (fine if you moved the repo — re-run install-bridge.sh to update)"
        fi
    else
        say_fail "bridge.py not found — re-run install-bridge.sh from the current checkout location"
    fi

    if [[ -n "$installed_secret" ]]; then
        if [[ -f "$SECRET_FILE" ]] && [[ "$(cat "$SECRET_FILE")" == "$installed_secret" ]]; then
            say_ok ".secret matches the plist"
        else
            say_fail ".secret file does not match the plist (delivery will get 401). Re-sync or re-install."
        fi
    else
        say_fail "no NOTES_BRIDGE_SECRET in the plist"
    fi

    echo "runtime:"
    if launchctl list | grep -q "$LABEL"; then
        say_ok "loaded in launchd"
    else
        say_fail "not loaded in launchd — run: launchctl bootstrap \"gui/\$(id -u)\" $PLIST_DEST"
    fi

    if curl -sf http://localhost:48213/health >/dev/null 2>&1; then
        health="$(curl -s http://localhost:48213/health)"
        say_ok "HTTP /health responded: $health"
        case "$health" in
            *reachable*not-reachable*)
                say_fail "bridge running but Notes is not reachable — likely Automation permission not granted"
                ;;
        esac
    else
        say_fail "HTTP /health did not respond — bridge is not listening on :48213"
    fi

    echo
    if [[ $fail -eq 0 ]]; then
        echo "all green."
        exit 0
    else
        echo "one or more checks failed — see FAIL lines above." >&2
        exit 1
    fi
fi

# --- install mode --------------------------------------------------------

if [[ ! -f "$BRIDGE_PY" ]]; then
    echo "error: $BRIDGE_PY not found" >&2
    exit 1
fi
if [[ ! -f "$PLIST_TEMPLATE" ]]; then
    echo "error: $PLIST_TEMPLATE not found" >&2
    exit 1
fi

# Pick a python for the LaunchAgent. Two constraints that eliminate the
# obvious defaults:
#   - /usr/bin/python3 is Xcode's TCC-sandboxed stub. It cannot read
#     files under ~/Documents/ when invoked from a LaunchAgent, so the
#     bridge would fail with "Operation not permitted" every time.
#   - conda/anaconda/miniconda pythons get removed cleanly when the user
#     uninstalls conda, silently leaving a dangling LaunchAgent behind.
#     They work, but need a warning.
#
# Preference order below tries stable, user-managed installs first.
_pick_python() {
    local c
    for c in \
        "$HOME/.local/bin/python3.13" \
        "$HOME/.local/bin/python3.12" \
        "$HOME/.local/bin/python3.11" \
        "$HOME/.local/bin/python3" \
        "/opt/homebrew/bin/python3" \
        "/usr/local/bin/python3"
    do
        if [[ -x "$c" ]]; then
            printf '%s' "$c"
            return 0
        fi
    done

    # Fall back to whatever's on PATH — but reject known-bad interpreters.
    local on_path
    on_path="$(command -v python3 || true)"
    case "$on_path" in
        "")
            return 1
            ;;
        /usr/bin/python3)
            # Xcode's — sandboxed. Explicit reject.
            return 1
            ;;
        */anaconda*|*/miniconda*|*/miniforge*)
            echo "warning: using conda-managed python at $on_path" >&2
            echo "         if you remove conda later, re-run install-bridge.sh" >&2
            printf '%s' "$on_path"
            return 0
            ;;
        *)
            printf '%s' "$on_path"
            return 0
            ;;
    esac
}

PYTHON_BIN="$(_pick_python)" || {
    echo "error: no suitable python3 found for the LaunchAgent." >&2
    echo "" >&2
    echo "install one of the following, then re-run this script:" >&2
    echo "    uv python install 3.12       # recommended" >&2
    echo "    brew install python@3.12" >&2
    echo "" >&2
    echo "note: /usr/bin/python3 (Xcode's) is not usable — it is TCC-sandboxed" >&2
    echo "      and cannot read the bridge script from a LaunchAgent context." >&2
    exit 1
}
echo "using python: $PYTHON_BIN"

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
