#!/usr/bin/env bash
# cec-control.sh - HDMI-CEC commands for TV power and input switching

cec_wake_and_select_input_best_effort() {
    [ "$CEC_ENABLED" = "true" ] || [ "$CEC_ENABLED" = "1" ] || return 0
    debug "CEC" "cec_wake_and_select_input_best_effort: checking tools"

    if have cec-client; then
        local ok=0 attempt
        debug "CEC" "Using cec-client (port=$CEC_HDMI_PORT)"
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

    if have cec-ctl && compgen -G "/dev/cec*" >/dev/null; then
        local adapter_args=()
        if [ -n "${CEC_ADAPTER:-}" ]; then
            adapter_args+=( -d "$CEC_ADAPTER" )
        fi
        debug "CEC" "Using cec-ctl (route-to=$CEC_COUCH_PORT)"
        cec-ctl "${adapter_args[@]}" --to 0 --image-view-on >/dev/null 2>&1 || true
        cec-ctl "${adapter_args[@]}" --to 0 --power-on >/dev/null 2>&1 || true
        cec-ctl "${adapter_args[@]}" --route-to "$CEC_COUCH_PORT" >/dev/null 2>&1 || true
        log "cec: attempted wake + route-to port $CEC_COUCH_PORT (cec-ctl ${CEC_ADAPTER:-auto})"
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
