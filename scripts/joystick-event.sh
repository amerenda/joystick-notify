#!/usr/bin/env bash
set -euo pipefail

LOG=/tmp/joystick-events.log
LOCK=/tmp/joystick-events.lock   # flock file
BT_DEBUG_LOG=/tmp/bluetooth-events.log

# Source config for DEBUG_BLUETOOTH setting
LIB_DIR="/usr/local/lib/joystick-notify"
if [ -f "$LIB_DIR/config-env.sh" ]; then
    # shellcheck disable=SC1091
    source "$LIB_DIR/config-env.sh"
fi
DEBUG_BLUETOOTH="${DEBUG_BLUETOOTH:-false}"

# udev normally sets ACTION, but we also override it in the udev rule to map bind/unbind -> add/remove.
ACT="${ACTION:-add}"

# We treat the arg as an opaque device identifier (typically HID_UNIQ / Bluetooth MAC).
DEV="${1:-unknown}"

# Ensure events log exists and is writable
if [ ! -e "$LOG" ]; then
  : >"$LOG"
  chown root:root "$LOG" || true
  chmod 666 "$LOG" || true
fi

# Ensure lock file exists and is writable by both root (udev) and the user service.
# Without this, monitor-switcher (running as user) may be unable to flock() the lock,
# causing synthetic grace_timeout/steam_exit events to be dropped.
if [ ! -e "$LOCK" ]; then
  ( umask 0; : >"$LOCK" ) 2>/dev/null || true
  chown root:root "$LOCK" || true
  chmod 666 "$LOCK" || true
else
  chmod 666 "$LOCK" || true
fi

# Append-only write with lock
{
  flock -n 9 || exit 0
  printf '%s %s %s\n' "$(date -Is)" "$ACT" "$DEV" >> "$LOG"
} 9>"$LOCK"

# Optional Bluetooth debug logging to track disconnect frequency
if [ "$DEBUG_BLUETOOTH" = "true" ]; then
    # Ensure BT debug log exists and is writable
    if [ ! -e "$BT_DEBUG_LOG" ]; then
        : >"$BT_DEBUG_LOG"
        chmod 666 "$BT_DEBUG_LOG" 2>/dev/null || true
    fi
    echo "$(date -Is) BT_EVENT: $ACT $DEV" >> "$BT_DEBUG_LOG"
fi
