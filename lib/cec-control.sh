#!/usr/bin/env bash
# cec-control.sh - HDMI-CEC commands for TV power and input switching

# Discover our CEC adapter's physical address from topology (e.g. 2.0.0.0).
# Prints the address to stdout and returns 0 if found; prints nothing and returns 1 otherwise.
# Uses cec-ctl --playback -s -S and parses the "Playback Device" line.
get_cec_phys_addr() {
    local dev out addr
    if [ -n "${CEC_ADAPTER:-}" ]; then
        dev="$CEC_ADAPTER"
        out="$(cec-ctl -d "$dev" --playback -s -S 2>/dev/null)" || return 1
        addr="$(echo "$out" | awk '/Playback/ { gsub(/:$/, "", $1); if ($1 ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/) { print $1; exit 0 } }')"
        [ -n "$addr" ] && echo "$addr" && return 0
        return 1
    fi
    for dev in /dev/cec*; do
        [ -e "$dev" ] || continue
        out="$(cec-ctl -d "$dev" --playback -s -S 2>/dev/null)" || continue
        addr="$(echo "$out" | awk '/Playback/ { gsub(/:$/, "", $1); if ($1 ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/) { print $1; exit 0 } }')"
        if [ -n "$addr" ]; then
            echo "$addr"
            return 0
        fi
    done
    return 1
}

cec_wake_and_select_input_best_effort() {
    [ "$CEC_ENABLED" = "true" ] || [ "$CEC_ENABLED" = "1" ] || return 0
    debug "CEC" "cec_wake_and_select_input_best_effort: checking tools"

    # Prefer cec-ctl: wake, then Set Stream Path + Active Source so receiver switches to correct input
    if have cec-ctl && compgen -G "/dev/cec*" >/dev/null; then
        local adapter_args=()
        [ -n "${CEC_ADAPTER:-}" ] && adapter_args+=( -d "$CEC_ADAPTER" )
        local addr
        if [ -n "${CEC_ACTIVE_SOURCE_PHYS_ADDR:-}" ]; then
            addr="$CEC_ACTIVE_SOURCE_PHYS_ADDR"
            debug "CEC" "Using cec-ctl (override phys-addr=$addr)"
        else
            addr="$(get_cec_phys_addr 2>/dev/null)" || true
            if [ -z "$addr" ] && [ -n "${CEC_COUCH_PORT:-}" ] && [ "$CEC_COUCH_PORT" -ge 1 ] 2>/dev/null && [ "$CEC_COUCH_PORT" -le 4 ] 2>/dev/null; then
                addr="${CEC_COUCH_PORT}.0.0.0"
                debug "CEC" "Using cec-ctl (fallback phys-addr=$addr from CEC_COUCH_PORT)"
            fi
        fi

        if [ -n "$addr" ]; then
            log "cec: wake + set-stream-path + active-source phys-addr=$addr"
            cec-ctl "${adapter_args[@]}" --to 0 --image-view-on >/dev/null 2>&1 || true
            cec-ctl "${adapter_args[@]}" --to 0 --power-on >/dev/null 2>&1 || true
            [ "${CEC_WAKE_DELAY:-0}" -gt 0 ] 2>/dev/null && sleep "$CEC_WAKE_DELAY"
            cec-ctl "${adapter_args[@]}" --to 0 --set-stream-path "phys-addr=$addr" >/dev/null 2>&1 || true
            cec-ctl "${adapter_args[@]}" --to 0 --active-source "phys-addr=$addr" >/dev/null 2>&1 || true
        else
            log "cec: input switch skipped (no address: set CEC_ACTIVE_SOURCE_PHYS_ADDR=2.0.0.0 for receiver HDMI 2)"
            cec-ctl "${adapter_args[@]}" --to 0 --image-view-on >/dev/null 2>&1 || true
        fi
        ( umask 077; : >"$CEC_STATE" ) 2>/dev/null || true
        return 0
    fi

    # cec-client only: use CEC_HDMI_PORT fallback
    if have cec-client; then
        local ok=0 attempt
        debug "CEC" "Using cec-client (port=$CEC_HDMI_PORT fallback)"
        for attempt in 1 2 3 4 5; do
            if printf 'as\nis\nq\n' | cec-client -s -d 1 -p "$CEC_HDMI_PORT" >/dev/null 2>&1; then
                ok=1
                break
            fi
            debug "CEC" "Attempt $attempt failed, retrying..."
            sleep 1
        done
        if [ "$ok" -eq 1 ]; then
            log "cec: active-source asserted (cec-client -p $CEC_HDMI_PORT)"
        else
            log "cec: warn: active-source failed (cec-client -p $CEC_HDMI_PORT)"
        fi
        ( umask 077; : >"$CEC_STATE" ) 2>/dev/null || true
        return 0
    fi

    log "cec: skipped (missing cec-ctl/cec-client)"
}

cec_standby_best_effort() {
    [ "$CEC_ENABLED" = "true" ] || [ "$CEC_ENABLED" = "1" ] || return 0
    [ "$CEC_POWER_OFF_ON_TEARDOWN" = "true" ] || [ "$CEC_POWER_OFF_ON_TEARDOWN" = "1" ] || return 0
    [ -e "$CEC_STATE" ] || return 0

    if have cec-client; then
        if printf 'standby 0\nq\n' | cec-client -s -d 1 -p "$CEC_HDMI_PORT" >/dev/null 2>&1; then
            log "cec: standby OK (cec-client -p $CEC_HDMI_PORT)"
        else
            log "cec: warn: standby failed (cec-client -p $CEC_HDMI_PORT)"
        fi
        return 0
    fi

    if have cec-ctl && compgen -G "/dev/cec*" >/dev/null; then
        local adapter_args=()
        if [ -n "${CEC_ADAPTER:-}" ]; then
            adapter_args+=( -d "$CEC_ADAPTER" )
        fi
        cec-ctl "${adapter_args[@]}" --to 0 --standby >/dev/null 2>&1 || true
        log "cec: standby sent (cec-ctl ${CEC_ADAPTER:-auto})"
        return 0
    fi
}
