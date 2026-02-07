#!/usr/bin/env bash
# couch-switch.sh - Manual couch/desk mode control
# Usage: couch-switch.sh [couch|desk|auto|status]
#
# This script allows manual override of the automatic controller-driven mode switching.
# When in manual mode, automatic events (controller connect/disconnect) are ignored
# until you return to automatic mode with 'auto'.

set -euo pipefail

LIB_DIR="/usr/local/lib/joystick-notify"
for lib in "$LIB_DIR"/*.sh; do
    # shellcheck disable=SC1090
    [ -f "$lib" ] && source "$lib"
done

case "${1:-status}" in
    couch)
        echo "couch" > "$MANUAL_LOCK"
        log "manual: switching to couch mode"
        # Trigger couch mode activation by writing synthetic event
        printf '%s %s %s\n' "$(date -Is)" "manual_couch" "manual" >> "$LOG"
        echo "Manual couch mode enabled. Run 'couch-switch.sh desk' or 'couch-switch.sh auto' to change."
        ;;
    desk)
        echo "desk" > "$MANUAL_LOCK"
        log "manual: switching to desk mode"
        # Trigger desk mode by writing synthetic event
        printf '%s %s %s\n' "$(date -Is)" "manual_desk" "manual" >> "$LOG"
        echo "Manual desk mode enabled. Run 'couch-switch.sh couch' or 'couch-switch.sh auto' to change."
        ;;
    auto)
        rm -f "$MANUAL_LOCK"
        log "manual: returning to automatic mode"
        echo "Automatic mode restored. Controller events will now trigger mode changes."
        ;;
    status)
        echo "=== Mode Status ==="
        if is_manual_couch; then
            echo "Override: manual couch (locked)"
        elif is_manual_desk; then
            echo "Override: manual desk (locked)"
        else
            echo "Override: none (automatic)"
        fi
        if is_couch_mode; then
            owner="$(cat "$LOCK" 2>/dev/null || echo "unknown")"
            echo "Current:  couch mode (owner: $owner)"
        else
            echo "Current:  desk mode"
        fi
        ;;
    *)
        echo "Usage: couch-switch.sh [couch|desk|auto|status]"
        echo ""
        echo "Commands:"
        echo "  couch   - Switch to couch mode and lock (ignores controller disconnect)"
        echo "  desk    - Switch to desk mode and lock (ignores controller connect)"
        echo "  auto    - Return to automatic controller-driven mode"
        echo "  status  - Show current mode and override status"
        exit 1
        ;;
esac
