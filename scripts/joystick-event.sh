#!/usr/bin/env bash
set -euo pipefail

# Base directories - must match lib/config-env.sh
JN_BASE=/tmp/joystick-notify
JN_LOGS="$JN_BASE/logs"
JN_LOCKS="$JN_BASE/locks"

LOG="$JN_LOGS/events.log"
LOCK="$JN_LOCKS/events.lock"   # flock file

# udev normally sets ACTION, but we also override it in the udev rule to map bind/unbind -> add/remove.
ACT="${ACTION:-add}"

# We treat the arg as an opaque device identifier (typically HID_UNIQ / Bluetooth MAC).
DEV="${1:-unknown}"

# Ensure directories exist with world-writable permissions
# (udev runs as root, user service runs as user - both need access)
for dir in "$JN_BASE" "$JN_LOGS" "$JN_LOCKS"; do
    if [ ! -d "$dir" ]; then
        mkdir -p "$dir" 2>/dev/null || true
        chmod 777 "$dir" 2>/dev/null || true
    fi
done

# Ensure events log exists and is writable
if [ ! -e "$LOG" ]; then
    : >"$LOG"
    chmod 666 "$LOG" 2>/dev/null || true
fi

# Ensure lock file exists and is writable by both root (udev) and the user service.
# Without this, monitor-switcher (running as user) may be unable to flock() the lock,
# causing synthetic grace_timeout/steam_exit events to be dropped.
if [ ! -e "$LOCK" ]; then
    ( umask 0; : >"$LOCK" ) 2>/dev/null || true
    chmod 666 "$LOCK" 2>/dev/null || true
else
    chmod 666 "$LOCK" 2>/dev/null || true
fi

# Append-only write with lock
{
    flock -n 9 || exit 0
    printf '%s %s %s\n' "$(date -Is)" "$ACT" "$DEV" >> "$LOG"
} 9>"$LOCK"
