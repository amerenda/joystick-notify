#!/usr/bin/env bash
# cec-fixup.sh - Work around a known pulse8-cec driver bug where the
# Pulse-Eight CEC USB adapter (firmware v12) intermittently fails to
# register /dev/cec0 - either never appearing on boot, or registering and
# then silently dropping later with no kernel log trace (kernel logs
# "probe with driver pulse8-cec failed with error -110" / ETIMEDOUT on the
# failing attempts). A plain driver unbind/rebind (detach from the Linux
# driver model) is not reliable here and can fail indefinitely once the
# adapter's firmware gets wedged. A real USB bus reset (USBDEVFS_RESET,
# via /usr/local/bin/usbreset) forces an actual SE0 reset signal on the
# wire, which reliably clears the wedged firmware state - confirmed
# 2026-07-30 after driver unbind/rebind failed 8 rounds in a row but a
# single usbreset call recovered it immediately. This does NOT require a
# physical unplug; it is scoped to this one device and does not disturb
# any other USB peripherals on the shared xHCI controller (uhubctl
# confirmed this root hub port has no software-controllable power rail,
# so a physical power-cycle isn't achievable in software - a bus reset is
# the strongest software-only lever available, and empirically sufficient).
set -uo pipefail

VENDOR="2548"
PRODUCT="1002"
STABILITY_WINDOW=5   # seconds /dev/cec0 must stay present to be trusted
MAX_ROUNDS=4
LOG_TAG="cec-fixup"
USBRESET="/usr/local/bin/usbreset"

log() { logger -t "$LOG_TAG" -- "$*"; }

find_usb_port() {
    local d
    for d in /sys/bus/usb/devices/*; do
        [ -f "$d/idVendor" ] || continue
        [ -f "$d/idProduct" ] || continue
        if [ "$(cat "$d/idVendor" 2>/dev/null)" = "$VENDOR" ] && [ "$(cat "$d/idProduct" 2>/dev/null)" = "$PRODUCT" ]; then
            basename "$d"
            return 0
        fi
    done
    return 1
}

usb_devnode() {
    local port="$1" busnum devnum
    busnum="$(cat "/sys/bus/usb/devices/$port/busnum" 2>/dev/null)" || return 1
    devnum="$(cat "/sys/bus/usb/devices/$port/devnum" 2>/dev/null)" || return 1
    printf '/dev/bus/usb/%03d/%03d\n' "$busnum" "$devnum"
}

cec_present() { [ -e /dev/cec0 ]; }

wait_for_cec() {
    local timeout="$1" i
    for ((i = 0; i < timeout; i++)); do
        cec_present && return 0
        sleep 1
    done
    cec_present
}

cec_stable() {
    local i
    for ((i = 0; i < STABILITY_WINDOW; i++)); do
        sleep 1
        cec_present || return 1
    done
    return 0
}

device_bound() { [ -e "/sys/bus/usb/drivers/usb/$1" ]; }

# Primary repair: a real USB bus reset. usbreset only works on a device
# still bound to the generic "usb" driver (the normal case when the
# adapter's firmware wedges - the top-level device stays bound, only the
# pulse8-cec probe on top of it fails); if something left it unbound,
# rebind first so the reset actually has a driver in place to reprobe it.
# Falls back to plain unbind/rebind if usbreset itself isn't available or
# the device node can't be resolved.
repair_device() {
    local port="$1" node

    if ! device_bound "$port"; then
        log "$port not bound to usb driver, binding first"
        echo "$port" > /sys/bus/usb/drivers/usb/bind 2>/dev/null || log "bind failed"
        sleep 2
    fi

    if [ -x "$USBRESET" ] && node="$(usb_devnode "$port")"; then
        log "resetting $node (usbreset)"
        "$USBRESET" "$node" >/dev/null 2>&1 && return 0
        log "usbreset failed on $node, falling back to unbind/rebind"
    else
        log "could not resolve device node for $port, falling back to unbind/rebind"
    fi

    log "unbinding $port"
    echo "$port" > /sys/bus/usb/drivers/usb/unbind 2>/dev/null || log "unbind failed"
    sleep 2
    log "binding $port"
    echo "$port" > /sys/bus/usb/drivers/usb/bind 2>/dev/null || log "bind failed"
}

for ((round = 1; round <= MAX_ROUNDS; round++)); do
    if [ "$round" -eq 1 ]; then
        log "round 1: checking for cec0 (waiting up to 15s for initial probe)"
        wait_for_cec 15
    else
        PORT="${PORT:-$(find_usb_port)}" || { log "Pulse-Eight CEC adapter (idVendor=$VENDOR idProduct=$PRODUCT) not found on USB bus, giving up"; exit 1; }
        log "round $round: repairing $PORT"
        repair_device "$PORT"
        wait_for_cec 8
    fi

    if ! cec_present; then
        log "round $round: cec0 did not appear"
        continue
    fi

    log "round $round: cec0 present, confirming it stays up for ${STABILITY_WINDOW}s"
    if cec_stable; then
        log "round $round: cec0 stable, done"
        exit 0
    fi
    log "round $round: cec0 flapped (registered then dropped), retrying"
done

log "gave up after $MAX_ROUNDS rounds, cec0 never became stable"
exit 1
