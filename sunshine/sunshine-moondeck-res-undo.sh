#!/bin/bash
# Sunshine app prep-cmd "undo" for the MoonDeckStream app entry — counterpart
# to sunshine-moondeck-res-prep.sh. Restores whatever display mode was active
# before the stream started.
#
# Install: sudo install -Dm0755 sunshine-moondeck-res-undo.sh /usr/local/bin/sunshine-moondeck-res-undo.sh

export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
export XDG_RUNTIME_DIR=/run/user/1000
export DISPLAY=:0
export WAYLAND_DISPLAY=wayland-0
export XAUTHORITY=$(systemctl --user show-environment 2>/dev/null | grep ^XAUTHORITY= | cut -d= -f2-)
export XDG_SESSION_TYPE=wayland

_LOG=/tmp/sunshine-moondeck-res-debug.log
STATE_FILE=/tmp/sunshine-moondeck-res-prev-$(id -u)

printf '[%s] sunshine-moondeck-res-undo.sh: START\n' "$(date '+%T')" >> "$_LOG" 2>/dev/null || true

if [ -f "$STATE_FILE" ]; then
    prev=$(cat "$STATE_FILE")
    output_name="${prev%%:*}"
    prev_mode_id="${prev##*:}"
    rm -f "$STATE_FILE"
    if [ -n "$output_name" ] && [ -n "$prev_mode_id" ]; then
        printf '[%s] sunshine-moondeck-res-undo.sh: restoring %s to mode %s\n' \
            "$(date '+%T')" "$output_name" "$prev_mode_id" >> "$_LOG" 2>/dev/null || true
        kscreen-doctor "output.${output_name}.mode.${prev_mode_id}" >> "$_LOG" 2>&1 || true
    fi
else
    printf '[%s] sunshine-moondeck-res-undo.sh: no state file, skipping\n' "$(date '+%T')" >> "$_LOG" 2>/dev/null || true
fi
