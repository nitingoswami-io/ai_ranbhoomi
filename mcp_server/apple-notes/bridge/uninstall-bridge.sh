#!/usr/bin/env bash
# Reverse of install-bridge.sh. Leaves .secret in place so re-install stays
# stable — delete it manually if you want to rotate.

set -euo pipefail

LABEL="com.local.notes-bridge"
PLIST_DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"

launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
rm -f "$PLIST_DEST"
echo "unloaded and removed $PLIST_DEST"
