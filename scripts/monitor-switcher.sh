#!/usr/bin/env bash
# monitor-switcher.sh - Main coordinator for joystick-notify
# Breaks down udev and synthetic events into Couch Mode or Desk Mode actions.
set -euo pipefail

# Library directory (where install.sh copies the modular components)
LIB_DIR="/usr/local/lib/joystick-notify"

# Source modular libraries
for lib in "$LIB_DIR"/*.sh; do
    # shellcheck disable=SC1090
    [ -f "$lib" ] && source "$lib"
done

# Ensure directories exist
ensure_jn_dirs

# --- Runtime State ---
# Track whether we've successfully activated couch mode in THIS service run.
# This helps us detect stale lock files that we couldn't delete.
COUCH_ACTIVATED_THIS_RUN=false

# --- Main Logic ---

launcher_exists() { [ -x "$LAUNCHER" ]; }

couch_mode_activate() {
    local dev="${1:-unknown}"
    debug "SWITCHER" "couch_mode_activate: dev=$dev"
    # #region agent log - branch tracking (hypotheses B,C,E)
    _dbg_log="${DEBUG_LOG_PATH:-/home/alex/projects/joystick-notify/.cursor/debug.log}"
    # #endregion

    # Don't activate if manual desk mode is active (unless this IS a manual couch request)
    if is_manual_desk && [ "$dev" != "manual" ]; then
        log "couch_mode_activate: blocked by manual desk mode ($dev)"
        printf '{"timestamp":%s,"location":"couch_mode_activate","message":"branch","data":{"branch":"manual_desk_block"},"hypothesisId":"E"}\n' "$(date +%s)000" >> "$_dbg_log" 2>/dev/null || true
        return 0
    fi
    
    # Debounce: wait 1 second and verify controller is still present
    # This prevents false triggers from brief connections (e.g., docking to charge)
    # Skip debounce for manual requests
    if [ "$dev" != "manual" ]; then
        debug "SWITCHER" "debounce: waiting 1s before activation ($dev)"
        sleep 1
        if ! id_present "$dev"; then
            log "couch_mode_activate: aborted - controller disconnected during debounce ($dev)"
            printf '{"timestamp":%s,"location":"couch_mode_activate","message":"branch","data":{"branch":"debounce_abort","dev":"%s"},"hypothesisId":"C"}\n' "$(date +%s)000" "$dev" >> "$_dbg_log" 2>/dev/null || true
            return 0
        fi
        debug "SWITCHER" "debounce: controller still present, proceeding ($dev)"
    fi

    cancel_pending_timer
    
    if [ -e "$LOCK" ]; then
        local current_owner
        current_owner="$(lock_owner)"
        
        # If lock exists but is empty, it's stale - remove it and do full activation
        if [ -z "$current_owner" ]; then
            log "lock: found empty lock file, clearing and doing full activation"
            rm -f "$LOCK" 2>/dev/null || true
        # If we haven't activated in this service run, the lock is stale (leftover from crash/reboot)
        elif [ "$COUCH_ACTIVATED_THIS_RUN" = "false" ]; then
            log "lock: found stale lock from previous session (owner=$current_owner), doing full activation"
            rm -f "$LOCK" 2>/dev/null || true
        else
            # Already in couch mode with valid owner (activated this run). Update owner ID but skip display/CEC setup.
            echo -n "$dev" > "$LOCK"
            log "lock: updated owner to $dev (resuming existing session)"
            printf '{"timestamp":%s,"location":"couch_mode_activate","message":"branch","data":{"branch":"resume_owner_update","dev":"%s"},"hypothesisId":"B"}\n' "$(date +%s)000" "$dev" >> "$_dbg_log" 2>/dev/null || true
            note "🎮 Controller Reconnected" "$dev (new owner)"
            start_steam_watcher
            return 0
        fi
    fi

    if acquire_lock "$dev"; then
        printf '{"timestamp":%s,"location":"couch_mode_activate","message":"branch","data":{"branch":"acquire_ok","dev":"%s"},"hypothesisId":"B"}\n' "$(date +%s)000" "$dev" >> "$_dbg_log" 2>/dev/null || true
        COUCH_ACTIVATED_THIS_RUN=true
        log "begin: couch_mode_activate ($dev)"
        debug "SWITCHER" "Applying couch mode settings..."
        set_dnd true
        save_and_switch_to_couch_activity_best_effort
        save_and_switch_to_couch_desktop_best_effort
        start_steam_watcher

        if launcher_exists; then
            ( cec_wake_and_select_input_best_effort ) >/dev/null 2>&1 &
            couch_mode_active
            sleep 1
            log "action: launch steam big picture ($LAUNCHER)"
            debug "SWITCHER" "Setting STEAM_COMPAT_COMMAND_PREFIX=/usr/local/bin/game-wrapper.sh for launch"
            export STEAM_COMPAT_COMMAND_PREFIX="/usr/local/bin/game-wrapper.sh"
            debug "SWITCHER" "Environment before launch: $(env | grep STEAM || true)"
            LOCKFILE="$LOCK" "$LAUNCHER" >/dev/null 2>&1 &

            # Audio retry loop to ensure Steam/game lands on couch output
            (
                for i in {1..10}; do
                    sleep 2
                    couch_sink="$(couch_sink_name)"
                    debug "SWITCHER" "Audio retry loop: couch_sink=$couch_sink"
                    [ -n "${couch_sink:-}" ] && set_audio_to_sink "$couch_sink"
                done
            ) >/dev/null 2>&1 &
        else
            log "warn: launcher missing/not executable: $LAUNCHER"
        fi
        note "🎮 Controller Connected" "$dev (owner)"
        log "end: couch_mode_activate"
    else
        log "info: add ignored (owner=$(lock_owner))"
        printf '{"timestamp":%s,"location":"couch_mode_activate","message":"branch","data":{"branch":"acquire_fail","dev":"%s","owner":"%s"},"hypothesisId":"B"}\n' "$(date +%s)000" "$dev" "$(lock_owner)" >> "$_dbg_log" 2>/dev/null || true
    fi
}

couch_mode_teardown() {
    local why="${1:-}"
    local dev="${2:-}"
    
    # Don't tear down if manual couch mode is active (unless this IS a manual desk request)
    if is_manual_couch && [ "$why" != "manual_desk" ]; then
        log "teardown: blocked by manual couch mode ($why $dev)"
        return 0
    fi
    
    log "teardown: $why ${dev:-}"
    cancel_pending_timer
    cancel_steam_watcher
    desk_mode_active
    restore_previous_desktop_best_effort
    restore_previous_activity_best_effort
    cec_standby_best_effort
    set_dnd false
    rm -f "$CEC_STATE" >/dev/null 2>&1 || true
    rm -f "$LOCK" || true
    note "🛑 Couch-mode Ended" "${why:-ended} ${dev:-}"
}

# --- Event Loop ---

# Wait until events file exists
while [ ! -e "$LOG" ]; do log "waiting for $LOG to appear..."; sleep 0.5; done
log "watcher started, tailing $LOG"

# Initial cleanup and state enforcement
check_stale_lock
if [ ! -f "$LOCK" ]; then
    desk_mode_active
fi

# Ensure watcher is running if we boot into an active session
[ -e "$LOCK" ] && start_steam_watcher || true

# Tail the event log and react
while IFS= read -r line; do
    ACT="$(awk '{print $2}' <<<"$line" 2>/dev/null || echo)"
    DEV="$(norm_id "$(awk '{print $3}' <<<"$line" 2>/dev/null || echo)")"
    [ -n "${ACT:-}" ] && [ -n "${DEV:-}" ] || continue
    debug "SWITCHER" "Event loop processing: $ACT $DEV"
    log "event: $ACT $DEV"

    case "$ACT" in
        add)
            # #region agent log - add event and lock state (hypotheses A,B,C,E)
            _dbg_log="${DEBUG_LOG_PATH:-/home/alex/projects/joystick-notify/.cursor/debug.log}"
            _owner_add="$(lock_owner)"
            _id_ok="false"; id_present "$DEV" && _id_ok="true"
            printf '{"timestamp":%s,"location":"monitor-switcher:add","message":"add event","data":{"act":"add","dev":"%s","owner":"%s","id_present":%s},"hypothesisId":"A,B,E"}\n' "$(date +%s)000" "$DEV" "$_owner_add" "$_id_ok" >> "$_dbg_log" 2>/dev/null || true
            # #endregion
            couch_mode_activate "$DEV"
            ;;
        remove)
            if [ ! -e "$LOCK" ]; then
                log "info: remove ignored (no couch-mode lock)"
                continue
            fi
            owner_now="$(lock_owner)"
            if [ "$owner_now" = "$DEV" ] || ! any_controller_present; then
                if is_game_running; then
                    log "remove: game is running -> staying in couch mode (dev=$DEV owner=$owner_now)"
                elif is_steam_running; then
                    log "remove: steam running -> scheduling grace teardown check (dev=$DEV owner=$owner_now)"
                    schedule_disconnect_grace "$DEV"
                else
                    log "remove: steam not running -> immediate teardown (dev=$DEV owner=$owner_now)"
                    couch_mode_teardown "controller_disconnect" "$DEV"
                fi
            else
                log "info: remove ignored (non-owner; owner=$owner_now)"
            fi
            ;;
        grace_timeout)
            [ -e "$LOCK" ] || { log "grace: timeout ignored (no lock)"; cancel_pending_timer; continue; }
            if any_controller_present; then
                log "grace: timeout ignored (controllers present)"
                cancel_pending_timer
                continue
            fi
            owner_now="$(lock_owner)"
            if [ -n "${owner_now:-}" ] && id_present "$owner_now"; then
                log "grace: timeout ignored (owner present: $owner_now)"
                cancel_pending_timer
                continue
            fi
            couch_mode_teardown "grace_timeout" "$DEV"
            ;;
        steam_exit)
            [ -e "$LOCK" ] && couch_mode_teardown "steam_exit" "$DEV" || log "steam: exit ignored (no lock)"
            ;;
        manual_couch)
            log "manual: couch mode requested"
            if [ ! -e "$LOCK" ]; then
                # Not in couch mode - activate it
                couch_mode_activate "manual"
            else
                log "manual: already in couch mode"
            fi
            ;;
        manual_desk)
            log "manual: desk mode requested"
            if [ -e "$LOCK" ]; then
                couch_mode_teardown "manual_desk" "manual"
            else
                log "manual: already in desk mode"
            fi
            ;;
    esac
done < <(stdbuf -oL -eL tail -F -n 0 "$LOG")
