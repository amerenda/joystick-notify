#!/bin/bash
# Sunshine app prep-cmd "do" for the MoonDeckStream app entry — switches the
# host display to the Steam Deck's native panel resolution (1280x800) before
# the stream starts, so games rendering at desktop/borderless resolution
# aren't letterboxed on the deck. Counterpart: sunshine-moondeck-res-undo.sh.
#
# Resolves XAUTHORITY at run time (not hardcoded) — sunshine.service can
# outlive a compositor restart, which would otherwise leave it pointing at a
# dead auth file, same class of bug fixed for moondeckbuddy.service.
#
# Install: sudo install -Dm0755 sunshine-moondeck-res-prep.sh /usr/local/bin/sunshine-moondeck-res-prep.sh

export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
export XDG_RUNTIME_DIR=/run/user/1000
export DISPLAY=:0
export WAYLAND_DISPLAY=wayland-0
export XAUTHORITY=$(systemctl --user show-environment 2>/dev/null | grep ^XAUTHORITY= | cut -d= -f2-)
export XDG_SESSION_TYPE=wayland

_LOG=/tmp/sunshine-moondeck-res-debug.log
STATE_FILE=/tmp/sunshine-moondeck-res-prev-$(id -u)
TARGET_WIDTH=1280
TARGET_HEIGHT=800

printf '\n[%s] sunshine-moondeck-res-prep.sh: START\n' "$(date '+%T')" >> "$_LOG" 2>/dev/null || true

info=$(kscreen-doctor --json 2>>"$_LOG")
output_name=$(echo "$info" | jq -r '.outputs[] | select(.enabled and .connected) | .name' | head -1)
prev_mode_id=$(echo "$info" | jq -r --arg name "$output_name" '.outputs[] | select(.name==$name) | .currentModeId')
target_mode_id=$(echo "$info" | jq -r --arg name "$output_name" --argjson w "$TARGET_WIDTH" --argjson h "$TARGET_HEIGHT" \
    '.outputs[] | select(.name==$name) | .modes[] | select(.size.width==$w and .size.height==$h) | .id' | head -1)

if [ -z "$output_name" ] || [ -z "$target_mode_id" ]; then
    printf '[%s] sunshine-moondeck-res-prep.sh: could not resolve output/mode (output=%s target=%s), skipping\n' \
        "$(date '+%T')" "$output_name" "$target_mode_id" >> "$_LOG" 2>/dev/null || true
    exit 0
fi

echo "${output_name}:${prev_mode_id}" > "$STATE_FILE"
printf '[%s] sunshine-moondeck-res-prep.sh: %s mode %s -> %s (%sx%s)\n' \
    "$(date '+%T')" "$output_name" "$prev_mode_id" "$target_mode_id" "$TARGET_WIDTH" "$TARGET_HEIGHT" >> "$_LOG" 2>/dev/null || true

kscreen-doctor "output.${output_name}.mode.${target_mode_id}" >> "$_LOG" 2>&1 || true
