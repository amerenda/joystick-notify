#!/usr/bin/env bash
# display-control.sh - Primary display switching logic

desk_mode_active() {
    log "begin: desk_mode_active"
    debug "DISPLAY" "Switching to desk output: port=$DESK_PORT mode=$DESK_MODE"
    set_audio_to_sink "$HEADSET_SINK"
    # Brief delay to let GPU/driver settle before display changes (AMD RDNA3 workaround)
    sleep 0.5
    # Use timeout to prevent hangs if driver is stuck
    timeout 10 kscreen-doctor \
        "output.${DESK_PORT}.enable" \
        "output.${DESK_PORT}.priority.1" \
        "output.${DESK_PORT}.mode.${DESK_MODE}" \
        "output.${DESK_PORT}.position.0,0" \
        "output.${COUCH_PORT}.disable" 2>/dev/null || debug "DISPLAY" "kscreen-doctor failed or timed out for desk"
    log "end: desk_mode_active"
}

couch_mode_active() {
    log "begin: couch_mode_active"
    debug "DISPLAY" "Switching to couch output: port=$COUCH_PORT mode=$COUCH_MODE"
    # Brief delay to let GPU/driver settle before display changes (AMD RDNA3 workaround)
    sleep 0.5
    # Use timeout to prevent hangs if driver is stuck
    timeout 10 kscreen-doctor \
        "output.${COUCH_PORT}.enable" \
        "output.${COUCH_PORT}.priority.1" \
        "output.${COUCH_PORT}.mode.${COUCH_MODE}" \
        "output.${COUCH_PORT}.position.0,0" \
        "output.${DESK_PORT}.disable" 2>/dev/null || debug "DISPLAY" "kscreen-doctor failed or timed out for couch"

    if couch_sink="$(resolve_couch_sink_with_wait)"; then
        log "audio: resolved couch sink -> $couch_sink"
        set_audio_to_sink "$couch_sink"
    else
        log "audio: warn: could not resolve Couch sink after waiting"
    fi
    log "end: couch_mode_active"
}
