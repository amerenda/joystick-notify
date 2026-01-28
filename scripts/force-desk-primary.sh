#!/usr/bin/env bash
# force-desk-primary.sh
# Enforces the desk monitor as primary AND DISABLES the couch output if couch-mode is not active.
set -euo pipefail

# Source config to get ports and is_couch_mode
LIB_DIR="/usr/local/lib/joystick-notify"
if [ -f "$LIB_DIR/config-env.sh" ]; then
    source "$LIB_DIR/config-env.sh"
fi

# Allow disabling this script via environment variable
FORCE_DESK_PRIMARY="${FORCE_DESK_PRIMARY:-true}"
if [ "$FORCE_DESK_PRIMARY" != "true" ]; then
    exit 0
fi

# Check if couch-mode is active
if is_couch_mode; then
    # Couch mode is active, do nothing.
    exit 0
fi

# Enforce desk monitor as primary (priority 1) and DISABLE the couch output
kscreen-doctor \
    "output.${DESK_PORT}.enable" \
    "output.${DESK_PORT}.priority.1" \
    "output.${DESK_PORT}.mode.${DESK_MODE}" \
    "output.${DESK_PORT}.position.0,0" \
    "output.${COUCH_PORT}.disable" 2>/dev/null || true

echo "[force-desk-primary] Desk monitor enforced; couch output disabled." >&2
