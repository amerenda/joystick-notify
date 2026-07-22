#!/bin/bash
# Sunshine app prep-cmd "do" for the MoonDeckStream app entry — switches the
# host display to whatever resolution/refresh rate the connecting client
# actually negotiated, not a hardcoded value. Sunshine exposes this to
# prep-cmd via SUNSHINE_CLIENT_WIDTH / SUNSHINE_CLIENT_HEIGHT / SUNSHINE_CLIENT_FPS
# env vars (same mechanism documented for kscreen-doctor prep-cmds:
# https://github.com/LizardByte/Sunshine/issues/2160) — so a phone, a PC, or
# a different Deck model all get matched automatically, no per-device config.
#
# kscreen-doctor's own "mode.WIDTHxHEIGHT@FPS" string syntax is unreliable on
# this libkscreen version (partial-parses, inconsistent) — so the actual mode
# is still set by numeric ID, but that ID is looked up dynamically every run
# via `kscreen-doctor --json` + jq against the client's requested size, never
# hardcoded. Counterpart: sunshine-moondeck-res-undo.sh.
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

printf '\n[%s] sunshine-moondeck-res-prep.sh: START (client requested %sx%s@%s)\n' \
    "$(date '+%T')" "${SUNSHINE_CLIENT_WIDTH:-?}" "${SUNSHINE_CLIENT_HEIGHT:-?}" "${SUNSHINE_CLIENT_FPS:-?}" >> "$_LOG" 2>/dev/null || true

if [ -z "$SUNSHINE_CLIENT_WIDTH" ] || [ -z "$SUNSHINE_CLIENT_HEIGHT" ]; then
    printf '[%s] sunshine-moondeck-res-prep.sh: no SUNSHINE_CLIENT_WIDTH/HEIGHT in env, skipping\n' "$(date '+%T')" >> "$_LOG" 2>/dev/null || true
    exit 0
fi

info=$(kscreen-doctor --json 2>>"$_LOG")
output_name=$(echo "$info" | jq -r '.outputs[] | select(.enabled and .connected) | .name' | head -1)
prev_mode_id=$(echo "$info" | jq -r --arg name "$output_name" '.outputs[] | select(.name==$name) | .currentModeId')

# Among modes matching the client's exact width/height, prefer the one whose
# refresh rate is closest to what the client asked for (falls back to just
# "closest size available" if no exact WxH match exists on this output).
target_mode_id=$(echo "$info" | jq -r \
    --arg name "$output_name" \
    --argjson w "$SUNSHINE_CLIENT_WIDTH" \
    --argjson h "$SUNSHINE_CLIENT_HEIGHT" \
    --argjson fps "${SUNSHINE_CLIENT_FPS:-60}" \
    '.outputs[] | select(.name==$name) | .modes
       | map(select(.size.width==$w and .size.height==$h))
       | sort_by((.refreshRate - $fps) | length)
       | .[0].id // empty')

if [ -z "$output_name" ] || [ -z "$target_mode_id" ]; then
    printf '[%s] sunshine-moondeck-res-prep.sh: no matching mode for %sx%s on %s, leaving resolution as-is\n' \
        "$(date '+%T')" "$SUNSHINE_CLIENT_WIDTH" "$SUNSHINE_CLIENT_HEIGHT" "${output_name:-?}" >> "$_LOG" 2>/dev/null || true
    exit 0
fi

echo "${output_name}:${prev_mode_id}" > "$STATE_FILE"
printf '[%s] sunshine-moondeck-res-prep.sh: %s mode %s -> %s (matched client request %sx%s@%s)\n' \
    "$(date '+%T')" "$output_name" "$prev_mode_id" "$target_mode_id" \
    "$SUNSHINE_CLIENT_WIDTH" "$SUNSHINE_CLIENT_HEIGHT" "${SUNSHINE_CLIENT_FPS:-?}" >> "$_LOG" 2>/dev/null || true

kscreen-doctor "output.${output_name}.mode.${target_mode_id}" >> "$_LOG" 2>&1 || true
