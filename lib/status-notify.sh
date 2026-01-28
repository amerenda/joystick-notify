#!/usr/bin/env bash
# status-notify.sh - Logging and user notification logic

ts()  { date -Is; }

log() {
    ( umask 0; [ -e "$WATCHLOG" ] || { : >"$WATCHLOG"; chmod 666 "$WATCHLOG"; };
    printf '%s %s\n' "$(ts)" "$*" >> "$WATCHLOG" )
}

debug() {
    local component="${1:-}"
    local msg="${2:-}"
    [ "$DEBUG_MODE" = "true" ] || return 0
    
    local toggle_var="DEBUG_${component^^}"
    if [ "${!toggle_var:-false}" = "true" ]; then
        log "DEBUG [$component] $msg"
    fi
}

note() {
    # Notifications are best-effort
    command -v notify-send >/dev/null 2>&1 || return 0
    notify-send -h string:x-canonical-private-synchronous:joystick-notify "$@" >/dev/null 2>&1 || true
}

set_dnd() {
    # KDE Plasma 6 Do Not Disturb toggle via DBus.
    local state="${1:-false}"
    have qdbus6 || return 0
    qdbus6 org.freedesktop.Notifications /org/freedesktop/Notifications \
        org.freedesktop.Notifications.DoNotDisturbMode "$state" >/dev/null 2>&1 || true
    log "dnd: set to $state"
}
