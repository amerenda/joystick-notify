#!/usr/bin/env bash
# watcher-process.sh - Background monitoring for Steam and games

PENDING_TIMER_PID=""
STEAM_WATCHER_PID=""

cancel_pending_timer() {
    if is_pid_alive "${PENDING_TIMER_PID:-}"; then
        kill "$PENDING_TIMER_PID" >/dev/null 2>&1 || true
    fi
    PENDING_TIMER_PID=""
}

cancel_steam_watcher() {
    if is_pid_alive "${STEAM_WATCHER_PID:-}"; then
        kill "$STEAM_WATCHER_PID" >/dev/null 2>&1 || true
    fi
    STEAM_WATCHER_PID=""
}

emit_event() {
    local act="${1:-}"
    local dev="${2:-synthetic}"
    [ -n "$act" ] || return 0

    if [ ! -e "$LOG" ]; then
        ( umask 0; : >"$LOG"; chmod 666 "$LOG" ) >/dev/null 2>&1 || true
    fi

    if {
        flock -w 2 9 || exit 1
        printf '%s %s %s\n' "$(date -Is)" "$act" "$dev" >> "$LOG"
    } 9>"$EVENTS_LOCK" 2>/dev/null; then
        return 0
    fi

    printf '%s %s %s\n' "$(date -Is)" "$act" "$dev" >> "$LOG" 2>/dev/null || true
}

is_steam_running() {
    pgrep -x steam >/dev/null 2>&1 || pgrep -f '/steam' >/dev/null 2>&1
}

is_game_running() {
    pgrep -x gamescope >/dev/null 2>&1
}

schedule_disconnect_grace() {
    local removed_dev="${1:-}"
    cancel_pending_timer

    (
        sleep "$DISCONNECT_GRACE"
        emit_event "grace_timeout" "${removed_dev:-timeout}"
    ) >/dev/null 2>&1 &
    PENDING_TIMER_PID=$!
    log "grace: scheduled ${DISCONNECT_GRACE}s (pid=$PENDING_TIMER_PID) for ${removed_dev:-unknown}"
}

start_steam_watcher() {
    if is_pid_alive "${STEAM_WATCHER_PID:-}"; then
        return 0
    fi

    (
        local seen_running=0 misses=0 seen_game=0
        while [ -e "$LOCK" ]; do
            if is_steam_running; then
                seen_running=1
                misses=0
                
                if is_game_running; then
                    seen_game=1
                else
                    if [ "$seen_game" -eq 1 ] && ! any_controller_present; then
                        log "steam: game exited and no controllers present -> starting grace teardown"
                        emit_event "grace_timeout" "game_exit_timeout"
                        exit 0
                    fi
                    seen_game=0
                fi
            else
                if [ "$seen_running" -eq 1 ]; then
                    misses=$((misses + 1))
                    if [ "$misses" -ge 2 ]; then
                        emit_event "steam_exit" "steam"
                        exit 0
                    fi
                fi
            fi
            sleep "$STEAM_POLL"
        done
    ) >/dev/null 2>&1 &
    STEAM_WATCHER_PID=$!
    log "steam: watcher started (pid=$STEAM_WATCHER_PID poll=${STEAM_POLL}s)"
}
