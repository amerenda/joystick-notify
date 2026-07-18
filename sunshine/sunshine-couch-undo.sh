#!/bin/bash
# Sunshine global_prep_cmd "undo" — restore previous desktop when stream ends.
# Counterpart to sunshine-couch-prep.sh.
#
# Install: sudo install -Dm0755 sunshine-couch-undo.sh /usr/local/bin/sunshine-couch-undo.sh

export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
export XDG_RUNTIME_DIR=/run/user/1000

_PREP_LOG=/tmp/sunshine-couch-debug.log
printf '[%s] sunshine-couch-undo.sh: START (session disconnect)\n' "$(date '+%T')" >> "$_PREP_LOG" 2>/dev/null || true

# Release the Inhibit() token so ksld can auto-lock again after the session ends.
INHIBIT_PID_FILE=/tmp/sunshine-screen-inhibit-pid-$(id -u)
if [ -f "$INHIBIT_PID_FILE" ]; then
    _pid=$(cat "$INHIBIT_PID_FILE" 2>/dev/null || true)
    kill "$_pid" 2>/dev/null && printf '[%s] sunshine-couch-undo.sh: inhibitor killed (pid=%s)\n' "$(date '+%T')" "$_pid" >> "$_PREP_LOG" 2>/dev/null || true
    rm -f "$INHIBIT_PID_FILE"
fi

prev=$(cat /tmp/sunshine-prev-desktop 2>/dev/null || echo "1")
rm -f /tmp/sunshine-prev-desktop
qdbus6 org.kde.KWin /KWin org.kde.KWin.setCurrentDesktop "${prev:-1}" 2>/dev/null || true

# Restore normal screen-lock behavior now that the stream has ended.
/usr/local/bin/couch-mode-screen-unlock.sh stop >> "$_PREP_LOG" 2>&1 || true
printf '[%s] sunshine-couch-undo.sh: DONE\n' "$(date '+%T')" >> "$_PREP_LOG" 2>/dev/null || true
