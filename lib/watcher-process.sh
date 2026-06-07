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
        local seen_running=0 misses=0 seen_game=0 no_ctrl_since=0
        while [ -e "$LOCK" ]; do
            if is_steam_running; then
                seen_running=1
                misses=0

                if is_game_running; then
                    seen_game=1
                    no_ctrl_since=0
                else
                    if [ "$seen_game" -eq 1 ] && ! any_controller_present; then
                        log "steam: game exited and no controllers present -> starting grace teardown"
                        emit_event "grace_timeout" "game_exit_timeout"
                        exit 0
                    fi
                    seen_game=0

                    # Auto-exit couch mode if steam is running but no controller for STEAM_NO_CONTROLLER_TIMEOUT seconds
                    if any_controller_present; then
                        no_ctrl_since=0
                    else
                        _now="$(date +%s)"
                        if [ "$no_ctrl_since" -eq 0 ]; then
                            no_ctrl_since="$_now"
                            log "steam: no controller detected, ${STEAM_NO_CONTROLLER_TIMEOUT}s idle timeout started"
                        elif [ "$(( _now - no_ctrl_since ))" -ge "$STEAM_NO_CONTROLLER_TIMEOUT" ]; then
                            log "steam: no controller for ${STEAM_NO_CONTROLLER_TIMEOUT}s -> exiting couch mode"
                            emit_event "steam_exit" "no_controller_timeout"
                            exit 0
                        fi
                    fi
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
