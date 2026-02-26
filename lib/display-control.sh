#!/usr/bin/env bash
# display-control.sh - Primary display switching logic

desk_mode_active() {
    log "begin: desk_mode_active"
    debug "DISPLAY" "Switching to desk output: port=$DESK_PORT mode=$DESK_MODE"
    set_audio_to_sink "$HEADSET_SINK"
    kscreen-doctor \
        "output.${DESK_PORT}.enable" \
        "output.${DESK_PORT}.priority.1" \
        "output.${DESK_PORT}.mode.${DESK_MODE}" \
        "output.${DESK_PORT}.position.0,0" \
        "output.${COUCH_PORT}.disable" 2>/dev/null || debug "DISPLAY" "kscreen-doctor failed for desk"
    log "end: desk_mode_active"
}

# Read DRM status for the given port (e.g. HDMI-A-1). Returns "connected" or "disconnected".
_couch_connector_status() {
    local port="${1:-$COUCH_PORT}"
    local c s
    for c in /sys/class/drm/card*-"$port"; do
        [ -d "$c" ] || continue
        [ -f "$c/status" ] || continue
        s="$(cat "$c/status" 2>/dev/null)"
        echo "$s"
        return 0
    done
    echo "unknown"
    return 1
}

# Ask the kernel to re-probe the couch connector (HDMI link). Can help when a receiver
# is slow to present EDID after CEC wakes it. Uses udev change to trigger DRM hotplug.
_trigger_couch_connector_rescan() {
    local c
    for c in /sys/class/drm/card*-"${COUCH_PORT}"; do
        [ -d "$c" ] || continue
        if udevadm trigger --action=change "$c" 2>/dev/null; then
            log "display: triggered DRM rescan for $COUCH_PORT"
        else
            log "display: DRM rescan skipped (udevadm failed; try as root: sudo udevadm trigger --action=change $c)"
        fi
        return 0
    done
    return 1
}

couch_mode_active() {
    log "begin: couch_mode_active"
    debug "DISPLAY" "Switching to couch output: port=$COUCH_PORT mode=$COUCH_MODE"

    # #region agent log - GPU/display state for debug (hypotheses A,B,E)
    _debug_log_path="${DEBUG_LOG_PATH:-/home/alex/projects/joystick-notify/.cursor/debug.log}"
    _kscreen="$(kscreen-doctor -o 2>&1)" || _kscreen="kscreen-doctor failed"
    _drm_list="$(ls -la /sys/class/drm/ 2>&1)" || _drm_list="ls drm failed"
    _connectors=""
    for _c in /sys/class/drm/card*-*; do
        [ -d "$_c" ] || continue
        _name="${_c##*/}"
        _status=""
        [ -f "$_c/status" ] && _status="$(cat "$_c/status" 2>/dev/null)" || _status="?"
        _connectors="$_connectors $_name=$_status"
    done
    _ts="$(date +%s)000"
    if command -v jq >/dev/null 2>&1; then
        jq -n --arg ts "$_ts" --arg loc "display-control.sh:couch_mode_active" --arg ks "$_kscreen" --arg drm "$_drm_list" --arg conn "${_connectors# }" '{timestamp: ($ts | tonumber), location: $loc, message: "GPU display state at couch_mode_active", data: {kscreen_output: $ks, drm_list: $drm, connector_status: $conn, couch_port: "'"$COUCH_PORT"'", desk_port: "'"$DESK_PORT"'"}, hypothesisId: "A"}'
    else
        printf '{"timestamp":%s,"location":"display-control.sh","message":"GPU display state","data":{"connector_status":"%s","couch_port":"%s","desk_port":"%s"},"hypothesisId":"A"}\n' "$_ts" "${_connectors# }" "$COUCH_PORT" "$DESK_PORT"
    fi >> "$_debug_log_path" 2>/dev/null || true
    # #endregion

    # If couch connector is disconnected, the receiver may need 10–20s after CEC wakes it
    # before it presents EDID. Trigger DRM rescan and retry with longer waits (no reboot needed).
    _trigger_couch_connector_rescan
    _attempt=1
    _max_attempts=6
    _retry_delay=4
    while [ "$_attempt" -le "$_max_attempts" ]; do
        _status="$(_couch_connector_status 2>/dev/null)"
        if [ "$_status" = "connected" ]; then
            break
        fi
        if [ "$_attempt" -lt "$_max_attempts" ]; then
            _trigger_couch_connector_rescan
            log "display: $COUCH_PORT is $_status (attempt $_attempt/$_max_attempts), waiting ${_retry_delay}s for receiver EDID"
            sleep "$_retry_delay"
        else
            log "display: $COUCH_PORT still $_status after $_max_attempts attempts - receiver may not be presenting EDID to the PC (try again in a few seconds or see README re EDID emulator)"
        fi
        _attempt=$((_attempt + 1))
    done

    kscreen-doctor \
        "output.${COUCH_PORT}.enable" \
        "output.${COUCH_PORT}.priority.1" \
        "output.${COUCH_PORT}.mode.${COUCH_MODE}" \
        "output.${COUCH_PORT}.position.0,0" \
        "output.${DESK_PORT}.disable" 2>/dev/null
    _kscreen_rc=$?
    # #region agent log - did couch output enable succeed? (hypothesis B)
    _debug_log_path="${DEBUG_LOG_PATH:-/home/alex/projects/joystick-notify/.cursor/debug.log}"
    printf '{"timestamp":%s,"location":"display-control.sh:after_kscreen","message":"kscreen-doctor couch result","data":{"exit_code":%s,"couch_port":"%s"},"hypothesisId":"B"}\n' "$(date +%s)000" "$_kscreen_rc" "$COUCH_PORT" >> "$_debug_log_path" 2>/dev/null || true
    # #endregion
    [ "$_kscreen_rc" -ne 0 ] && debug "DISPLAY" "kscreen-doctor failed for couch"

    if couch_sink="$(resolve_couch_sink_with_wait)"; then
        log "audio: resolved couch sink -> $couch_sink"
        set_audio_to_sink "$couch_sink"
    else
        log "audio: warn: could not resolve Couch sink after waiting"
    fi
    log "end: couch_mode_active"
}
