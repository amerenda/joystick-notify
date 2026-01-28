#!/usr/bin/env bash
set -euo pipefail

LOG=/tmp/joystick-events.log
LOCK=/tmp/joystick-events.lock   # flock file

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
