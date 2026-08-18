#!/usr/bin/env bash
# display-control.sh - Primary display switching logic

# Query whether the given output is enabled at the compositor level (KWin's
# view via kscreen-doctor), not just whether the kscreen-doctor command
# exited 0. 2026-07-30 incident: a desk-mode switch logged as completed but
# the enable never actually took effect (silent failure, output stayed
# disabled) - a zero exit code alone is not proof the change applied.
_output_is_enabled() {
    local port="$1"
    kscreen-doctor -j 2>/dev/null | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(1)
for o in data.get('outputs', []):
    if o.get('name') == '$port':
        sys.exit(0 if o.get('enabled') else 1)
sys.exit(1)
" 2>/dev/null
}

desk_mode_active() {
    log "begin: desk_mode_active"
    debug "DISPLAY" "Switching to desk output: port=$DESK_PORT mode=$DESK_MODE"
    set_audio_to_sink "$HEADSET_SINK"

    _attempt=1
    _max_attempts=5
    _retry_delay=1
    while [ "$_attempt" -le "$_max_attempts" ]; do
        # Brief delay to let GPU/driver settle before display changes (AMD RDNA3 workaround)
        sleep 0.5
        # Use timeout to prevent hangs if driver is stuck
        _kscreen_err="$(timeout 10 kscreen-doctor \
            "output.${DESK_PORT}.enable" \
            "output.${DESK_PORT}.priority.1" \
            "output.${DESK_PORT}.mode.${DESK_MODE}" \
            "output.${DESK_PORT}.position.0,0" \
            "output.${COUCH_PORT}.disable" 2>&1)"
        _kscreen_rc=$?

        if [ "$_kscreen_rc" -eq 0 ] && _output_is_enabled "$DESK_PORT"; then
            log "display: $DESK_PORT active (attempt $_attempt/$_max_attempts)"
            break
        fi

        if [ "$_attempt" -lt "$_max_attempts" ]; then
            log "display: WARN - desk switch to $DESK_PORT did not take (attempt $_attempt/$_max_attempts, kscreen rc=$_kscreen_rc): ${_kscreen_err:-no output}. Retrying in ${_retry_delay}s"
            sleep "$_retry_delay"
        else
            log "display: ERROR - failed to switch to $DESK_PORT after $_max_attempts attempts, giving up: ${_kscreen_err:-no output}"
        fi
        _attempt=$((_attempt + 1))
    done
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
        if sudo -n udevadm trigger --action=change "$c" 2>/dev/null; then
            log "display: triggered DRM rescan for $COUCH_PORT"
        else
            log "display: DRM rescan failed (missing sudoers rule? install.sh adds /etc/sudoers.d/joystick-notify)"
        fi
        return 0
    done
    return 1
}

couch_mode_active() {
    log "begin: couch_mode_active"
    debug "DISPLAY" "Switching to couch output: port=$COUCH_PORT mode=$COUCH_MODE"

    # If couch connector is disconnected, the receiver may need 10–20s after CEC wakes it
    # before it presents EDID. Allow 5s initial settle time for CEC wake + receiver EDID,
    # then trigger DRM rescan and retry with longer waits (no reboot needed).
    log "display: waiting 5s for receiver to respond after CEC wake..."
    sleep 5
    _trigger_couch_connector_rescan
    _attempt=1
    _max_attempts=15
    _retry_delay=2
    while [ "$_attempt" -le "$_max_attempts" ]; do
        _status="$(_couch_connector_status 2>/dev/null)"
        if [ "$_status" = "connected" ]; then
            log "display: $COUCH_PORT connected (attempt $_attempt/$_max_attempts)"
            break
        fi
        if [ "$_attempt" -lt "$_max_attempts" ]; then
            _trigger_couch_connector_rescan
            log "display: $COUCH_PORT is $_status (attempt $_attempt/$_max_attempts), waiting ${_retry_delay}s for receiver EDID"
            sleep "$_retry_delay"
        else
            log "display: FAILED - $COUCH_PORT still $_status after $_max_attempts attempts. Exiting couch mode."
            log "display: TV/receiver may not be responding to CEC or presenting EDID. Returning to desk mode."
            return 1
        fi
        _attempt=$((_attempt + 1))
    done

    _attempt=1
    _max_attempts=5
    _retry_delay=1
    while [ "$_attempt" -le "$_max_attempts" ]; do
        # Brief delay to let GPU/driver settle before display changes (AMD RDNA3 workaround)
        sleep 0.5
        # Use timeout to prevent hangs if driver is stuck
        _kscreen_err="$(timeout 10 kscreen-doctor \
            "output.${COUCH_PORT}.enable" \
            "output.${COUCH_PORT}.priority.1" \
            "output.${COUCH_PORT}.mode.${COUCH_MODE}" \
            "output.${COUCH_PORT}.position.0,0" \
            "output.${DESK_PORT}.disable" 2>&1)"
        _kscreen_rc=$?

        if [ "$_kscreen_rc" -eq 0 ] && _output_is_enabled "$COUCH_PORT"; then
            log "display: $COUCH_PORT active (attempt $_attempt/$_max_attempts)"
            break
        fi

        if [ "$_attempt" -lt "$_max_attempts" ]; then
            log "display: WARN - couch switch to $COUCH_PORT did not take (attempt $_attempt/$_max_attempts, kscreen rc=$_kscreen_rc): ${_kscreen_err:-no output}. Retrying in ${_retry_delay}s"
            sleep "$_retry_delay"
        else
            log "display: FAILED - kscreen-doctor failed to switch display to $COUCH_PORT after $_max_attempts attempts: ${_kscreen_err:-no output}. Returning to desk mode."
            return 1
        fi
        _attempt=$((_attempt + 1))
    done

    if couch_sink="$(resolve_couch_sink_with_wait)"; then
        log "audio: resolved couch sink -> $couch_sink"
        set_audio_to_sink "$couch_sink"
    else
        log "audio: warn: could not resolve Couch sink after waiting"
    fi
    log "end: couch_mode_active"
}
