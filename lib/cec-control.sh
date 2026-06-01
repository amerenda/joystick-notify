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

    # cec-client fallback (Pulse-Eight USB and similar).
    # Wake first, apply CEC_WAKE_DELAY, then broadcast Active Source with the correct physical address.
    if have cec-client; then
        local ok=0 attempt
        debug "CEC" "Using cec-client (port=$CEC_HDMI_PORT)"
        # Step 1: Power on TV (addr 0) and receiver/audio system (addr 5).
        # cec-client's "as" handles Image View On internally, but we need to wake
        # the receiver explicitly since it's a separate logical device.
        printf 'on 0\non 5\nq\n' | cec-client -s -d 1 -p "$CEC_HDMI_PORT" >/dev/null 2>&1 || true
        log "cec: power-on sent to TV+receiver (cec-client -p $CEC_HDMI_PORT)"
        # Step 2: Wait for receiver to fully wake before asserting active source.
        [ "${CEC_WAKE_DELAY:-0}" -gt 0 ] 2>/dev/null && sleep "$CEC_WAKE_DELAY"
        # Step 3: Active Source — broadcast the correct physical address so TV routes
        # to the receiver's HDMI port where the PC is connected.
        # cec-client's built-in "as" always uses the adapter's own address (2.0.0.0 for
        # a Pulse-Eight on TV port 2), which is wrong when the PC is behind a receiver.
        # Use a raw CEC frame instead: 1F:82:{addr_hi}:{addr_lo}
        #   1F = source 1 (Recorder/Pulse-Eight), dest F (broadcast)
        #   82 = Active Source opcode
        local tx_cmd="as"
        if [ -n "${CEC_ACTIVE_SOURCE_PHYS_ADDR:-}" ]; then
            local hex_addr
            hex_addr="$(printf '%s' "$CEC_ACTIVE_SOURCE_PHYS_ADDR" | awk -F. '{printf "%X%X:%X%X", $1, $2, $3, $4}')"
            tx_cmd="tx 1F:82:${hex_addr}"
            debug "CEC" "Using raw Active Source tx for addr $CEC_ACTIVE_SOURCE_PHYS_ADDR (hex=$hex_addr)"
        fi
        for attempt in 1 2 3 4 5; do
            if printf '%s\nq\n' "$tx_cmd" | cec-client -s -d 1 -p "$CEC_HDMI_PORT" >/dev/null 2>&1; then
                ok=1
                break
            fi
            debug "CEC" "active-source attempt $attempt failed, retrying..."
            sleep 1
        done
        if [ "$ok" -eq 1 ]; then
            log "cec: active-source sent (cec-client -p $CEC_HDMI_PORT addr=${CEC_ACTIVE_SOURCE_PHYS_ADDR:-own})"
        else
            log "cec: warn: active-source failed after 5 attempts (cec-client -p $CEC_HDMI_PORT)"
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

    # Mirror wake's tool preference: cec-ctl first (kernel CEC), cec-client fallback (Pulse-Eight USB).
    if have cec-ctl && compgen -G "/dev/cec*" >/dev/null; then
        local adapter_args=()
        [ -n "${CEC_ADAPTER:-}" ] && adapter_args+=( -d "$CEC_ADAPTER" )
        cec-ctl "${adapter_args[@]}" --to 0 --standby >/dev/null 2>&1 || true
        log "cec: standby sent (cec-ctl ${CEC_ADAPTER:-auto})"
        return 0
    fi

    if have cec-client; then
        if printf 'standby 0\nq\n' | cec-client -s -d 1 -p "$CEC_HDMI_PORT" >/dev/null 2>&1; then
            log "cec: standby OK (cec-client -p $CEC_HDMI_PORT)"
        else
            log "cec: warn: standby failed (cec-client -p $CEC_HDMI_PORT)"
        fi
        return 0
    fi
}
