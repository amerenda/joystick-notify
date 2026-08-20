#!/usr/bin/env bash
# cec-control.sh - HDMI-CEC commands for TV power and input switching

# Discover our CEC adapter's own physical address (e.g. 4.0.0.0) from its
# Driver Info block. Prints the address to stdout and returns 0 if found;
# prints nothing and returns 1 otherwise.
#
# 2026-08-16: previously used `cec-ctl --playback -s -S` (-s = --skip-info,
# which hides the Driver Info block containing our own "Physical Address")
# and fell back to scanning the topology dump for any line mentioning
# "Playback", taking the first dotted-quad match. That's wrong whenever
# another Playback Device exists on the CEC bus (e.g. a Shield behind the
# receiver): its topology line ("3.1.0.0: Playback Device 2") sorted before
# our own adapter's line ("4.0.0.0: Playback Device 1"), so Active Source
# was broadcast with someone else's address, silently pointing the TV at the
# wrong HDMI input.
#
# Fix: drop --playback (redundant - cec0-configure@.service already
# configures the adapter as a playback device on attach; re-asserting it
# here needlessly touches logical-address negotiation on every wake) and -s
# (we need the Driver Info block), then take the FIRST "Physical Address"
# line in the output - that's always our own adapter's Driver Info, which is
# printed before any other device's "System Information" block.
get_cec_phys_addr() {
    local dev out addr
    if [ -n "${CEC_ADAPTER:-}" ]; then
        dev="$CEC_ADAPTER"
        out="$(cec-ctl -d "$dev" -S 2>/dev/null)" || return 1
        # Require a colon after the label: cec-ctl's "Capabilities" block also
        # lists "Physical Address" as a bare capability-name bullet with no
        # value, which would otherwise match first.
        addr="$(echo "$out" | awk '/Physical Address[[:space:]]*:/ { print $NF; exit }')"
        [ -n "$addr" ] && echo "$addr" && return 0
        return 1
    fi
    for dev in /dev/cec*; do
        [ -e "$dev" ] || continue
        out="$(cec-ctl -d "$dev" -S 2>/dev/null)" || continue
        # Require a colon after the label: cec-ctl's "Capabilities" block also
        # lists "Physical Address" as a bare capability-name bullet with no
        # value, which would otherwise match first.
        addr="$(echo "$out" | awk '/Physical Address[[:space:]]*:/ { print $NF; exit }')"
        if [ -n "$addr" ]; then
            echo "$addr"
            return 0
        fi
    done
    return 1
}

# Surface a broken adapter instead of retrying forever in silence: the tray
# icon (system-tray/joystick-tray.py) shows a red-X badge while CEC_BROKEN_FLAG
# exists. Fires one desktop notification on the OK->broken transition only
# (not on every failed attempt), so repeated reconnects don't spam notifications.
cec_mark_broken_best_effort() {
    local was_broken=0
    [ -e "$CEC_BROKEN_FLAG" ] && was_broken=1
    ( umask 0; : >"$CEC_BROKEN_FLAG"; chmod 666 "$CEC_BROKEN_FLAG" ) 2>/dev/null || true
    [ "$was_broken" -eq 0 ] && note "⚠️ CEC not working" "TV won't auto power-on/switch input. See tray icon or: journalctl --user -u joystick-notify.service"
}

cec_mark_ok_best_effort() {
    [ -e "$CEC_BROKEN_FLAG" ] || return 0
    rm -f "$CEC_BROKEN_FLAG" 2>/dev/null || true
    log "cec: adapter healthy again, cleared broken flag"
    note "✅ CEC recovered" "TV auto power-on/input-switch is working again"
}

# Self-heal: the pulse8-cec line discipline (inputattach --daemon, see
# pulse8-cec-attach@.service) has been observed to silently exit minutes
# after a clean attach, dropping /dev/cec0 with no kernel log trace and no
# USB re-enumeration event, so pulse8-cec-autoattach.rules never re-fires.
# cec-watchdog.timer catches this within 2 minutes, but a couch-mode
# activation shouldn't have to wait that long for its wake window: if
# /dev/cec0 is missing right when we need it, reattach synchronously first.
#
# Rate-limited to once per CEC_SELFHEAL_COOLDOWN: without this, repeated
# controller reconnects (e.g. while someone is actively debugging the
# adapter) would each trigger a fresh reattach + USB-bus-reset escalation,
# which is disruptive precisely when a human might be mid-troubleshooting.
cec_ensure_adapter_best_effort() {
    if compgen -G "/dev/cec*" >/dev/null; then
        cec_mark_ok_best_effort
        return 0
    fi

    local now last age
    now="$(date +%s)"
    last="$(cat "$CEC_SELFHEAL_STATE" 2>/dev/null || echo 0)"
    age=$(( now - last ))
    if [ "$age" -lt "${CEC_SELFHEAL_COOLDOWN:-120}" ]; then
        log "cec: /dev/cec0 missing, self-heal on cooldown (${age}s/${CEC_SELFHEAL_COOLDOWN:-120}s) - proceeding without CEC"
        cec_mark_broken_best_effort
        return 1
    fi
    ( umask 077; echo "$now" >"$CEC_SELFHEAL_STATE" ) 2>/dev/null || true

    log "cec: /dev/cec0 missing, self-healing (cec-watchdog.sh) before use"
    sudo -n /usr/local/bin/cec-watchdog.sh >/dev/null 2>&1 || true
    local _i
    for _i in 1 2 3 4 5; do
        if compgen -G "/dev/cec*" >/dev/null; then
            log "cec: self-heal recovered /dev/cec0"
            cec_mark_ok_best_effort
            return 0
        fi
        sleep 1
    done
    log "cec: self-heal failed, /dev/cec0 still missing"
    cec_mark_broken_best_effort
    return 1
}

cec_wake_and_select_input_best_effort() {
    [ "$CEC_ENABLED" = "true" ] || [ "$CEC_ENABLED" = "1" ] || return 0
    debug "CEC" "cec_wake_and_select_input_best_effort: checking tools"
    cec_ensure_adapter_best_effort

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
            # Note: cec-ctl has no separate "--power-on" opcode; Image View On
            # is the standard One Touch Play wake command and is sufficient
            # on its own (confirmed 2026-08-16: TV replied
            # REPORT_POWER_STATUS pwr-state=to-on after --image-view-on alone).
            cec-ctl "${adapter_args[@]}" --to 0 --image-view-on >/dev/null 2>&1 || true
            [ "${CEC_WAKE_DELAY:-0}" -gt 0 ] 2>/dev/null && sleep "$CEC_WAKE_DELAY"
            cec-ctl "${adapter_args[@]}" --to 0 --set-stream-path "phys-addr=$addr" >/dev/null 2>&1 || true
            cec-ctl "${adapter_args[@]}" --to 0 --active-source "phys-addr=$addr" >/dev/null 2>&1 || true
            # Re-assert active source in background to reclaim input from competing CEC devices
            # (e.g. Nvidia Shield waking and sending its own Active Source on cold start).
            if [ "${CEC_ACTIVE_SOURCE_RETRIES:-2}" -gt 0 ] 2>/dev/null; then
                (
                    for _r in $(seq 1 "${CEC_ACTIVE_SOURCE_RETRIES:-2}"); do
                        sleep "${CEC_ACTIVE_SOURCE_RETRY_DELAY:-4}"
                        # Cooperative cancellation: skip (and stop) if desk mode has taken
                        # over since this retry was scheduled. Without this, a stale retry
                        # can fire after teardown/standby and re-wake the TV to this input
                        # with no display output behind it (confirmed 2026-08-19).
                        if [ "$(cat "${LAST_MODE_FILE:-/dev/null}" 2>/dev/null)" != "couch" ]; then
                            log "cec: active-source re-assert (retry $_r/${CEC_ACTIVE_SOURCE_RETRIES:-2}) skipped - no longer in couch mode"
                            break
                        fi
                        log "cec: active-source re-assert (retry $_r/${CEC_ACTIVE_SOURCE_RETRIES:-2}) phys-addr=$addr"
                        cec-ctl "${adapter_args[@]}" --to 0 --set-stream-path "phys-addr=$addr" >/dev/null 2>&1 || true
                        cec-ctl "${adapter_args[@]}" --to 0 --active-source "phys-addr=$addr" >/dev/null 2>&1 || true
                    done
                ) &
            fi
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
            # Re-assert active source in background to reclaim input from competing CEC devices.
            if [ "${CEC_ACTIVE_SOURCE_RETRIES:-2}" -gt 0 ] 2>/dev/null; then
                (
                    for _r in $(seq 1 "${CEC_ACTIVE_SOURCE_RETRIES:-2}"); do
                        sleep "${CEC_ACTIVE_SOURCE_RETRY_DELAY:-4}"
                        # Cooperative cancellation: see cec-ctl branch above for rationale.
                        if [ "$(cat "${LAST_MODE_FILE:-/dev/null}" 2>/dev/null)" != "couch" ]; then
                            log "cec: active-source re-assert (retry $_r/${CEC_ACTIVE_SOURCE_RETRIES:-2}) skipped - no longer in couch mode"
                            break
                        fi
                        log "cec: active-source re-assert (retry $_r/${CEC_ACTIVE_SOURCE_RETRIES:-2})"
                        printf '%s\nq\n' "$tx_cmd" | cec-client -s -d 1 -p "$CEC_HDMI_PORT" >/dev/null 2>&1 || true
                    done
                ) &
            fi
        else
            log "cec: warn: active-source failed after 5 attempts (cec-client -p $CEC_HDMI_PORT)"
        fi
        ( umask 077; : >"$CEC_STATE" ) 2>/dev/null || true
        return 0
    fi

    log "cec: skipped (missing cec-ctl/cec-client)"
}

cec_allm_best_effort() {
    local mode="${1:-on}"  # on | off
    [ "$CEC_ENABLED" = "true" ] || [ "$CEC_ENABLED" = "1" ] || return 0
    [ "${CEC_ALLM_ENABLED:-true}" = "true" ] || return 0
    cec_ensure_adapter_best_effort
    have cec-ctl && compgen -G "/dev/cec*" >/dev/null || { log "cec: ALLM skipped (no cec-ctl)"; return 0; }

    local adapter_args=()
    [ -n "${CEC_ADAPTER:-}" ] && adapter_args+=( -d "$CEC_ADAPTER" )
    local addr
    if [ -n "${CEC_ACTIVE_SOURCE_PHYS_ADDR:-}" ]; then
        addr="$CEC_ACTIVE_SOURCE_PHYS_ADDR"
    else
        addr="$(get_cec_phys_addr 2>/dev/null)" || true
    fi
    if [ -z "${addr:-}" ]; then
        log "cec: ALLM skipped (no physical address)"
        return 0
    fi

    cec-ctl "${adapter_args[@]}" \
        --report-current-latency \
        "phys-addr=${addr},video-latency=1,low-latency-mode=${mode},audio-out-compensated=na,audio-out-delay=0" \
        >/dev/null 2>&1 || true
    log "cec: ALLM ${mode} (phys-addr=${addr})"
}

cec_standby_best_effort() {
    [ "$CEC_ENABLED" = "true" ] || [ "$CEC_ENABLED" = "1" ] || return 0
    [ "$CEC_POWER_OFF_ON_TEARDOWN" = "true" ] || [ "$CEC_POWER_OFF_ON_TEARDOWN" = "1" ] || return 0
    [ -e "$CEC_STATE" ] || return 0
    cec_ensure_adapter_best_effort

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
