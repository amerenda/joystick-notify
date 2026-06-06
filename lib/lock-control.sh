#!/usr/bin/env bash
# lock-control.sh - Lock management and HID detection

lock_owner() { [ -f "$LOCK" ] && cat "$LOCK" || true; }
norm_id() { tr 'A-F' 'a-f' <<<"${1:-}"; }

acquire_lock() {
    local dev
    dev="$(norm_id "${1:-}")"
    ( umask 0; set -o noclobber; echo -n "$dev" > "$LOCK" ) 2>/dev/null || { log "lock: already owned by $(lock_owner)"; return 1; }
    chmod 666 "$LOCK" || true
    log "lock: acquired by $dev"
}

release_lock_if_owner() {
    local dev
    dev="$(norm_id "${1:-}")"
    if [ "$(lock_owner)" = "$dev" ]; then
        rm -f "$LOCK"
        log "lock: released by $dev"
    else
        log "lock: release skipped (owner=$(lock_owner), dev=$dev)"
    fi
}

# --- HID presence helpers ---
hid_uniq_present() {
    local mac
    mac="$(norm_id "${1:-}")"
    [ -n "$mac" ] || return 1
    local f
    for f in /sys/bus/hid/devices/*/uevent; do
        [ -e "$f" ] || continue
        if grep -qi "^HID_UNIQ=${mac}$" "$f" 2>/dev/null; then
            return 0
        fi
    done
    return 1
}

usb_vidpid_present() {
    local vid="${1:-}"
    local pid="${2:-}"
    [ -n "$vid" ] && [ -n "$pid" ] || return 1
    local d
    for d in /sys/bus/usb/devices/*; do
        [ -e "$d/idVendor" ] || continue
        [ -e "$d/idProduct" ] || continue
        if [ "$(tr 'A-F' 'a-f' <"$d/idVendor" 2>/dev/null)" = "$vid" ] && \
           [ "$(tr 'A-F' 'a-f' <"$d/idProduct" 2>/dev/null)" = "$pid" ]; then
            return 0
        fi
    done
    return 1
}

id_present() {
    local id
    id="$(norm_id "${1:-}")"
    [ -n "$id" ] || return 1

    if [[ "$id" == event* ]]; then
        [ -e "/dev/input/$id" ]
        return $?
    fi

    if [[ "$id" == js* ]]; then
        [ -e "/dev/input/$id" ]
        return $?
    fi

    if [[ "$id" == usb:*:* ]]; then
        local vid pid
        vid="$(cut -d: -f2 <<<"$id" 2>/dev/null || true)"
        pid="$(cut -d: -f3 <<<"$id" 2>/dev/null || true)"
        usb_vidpid_present "$vid" "$pid"
        return $?
    fi

    hid_uniq_present "$id"
}

list_present_controller_uniq() {
    local f name uniq
    for f in /sys/bus/hid/devices/*/uevent; do
        [ -e "$f" ] || continue
        name="$(grep -m1 '^HID_NAME=' "$f" 2>/dev/null | cut -d= -f2- || true)"
        uniq="$(grep -m1 '^HID_UNIQ=' "$f" 2>/dev/null | cut -d= -f2- || true)"
        # Match the same patterns as the udev rules (99-joystick-notify.rules)
        [[ "$name" == *Controller* ]] || [[ "$name" == *Gamepad* ]] || [[ "$name" == *8BitDo* ]] || continue
        # Exclude LED, light, and other non-game peripherals
        [[ "$name" == *LED* ]] && continue
        [[ "$name" == *Light* ]] && continue
        [[ "$name" == *Lighting* ]] && continue
        # UNIQ can be Bluetooth MAC (e4:17:d8:bb:e0:03) or hex string (C5E29E249D)
        [ -n "$uniq" ] || continue
        echo "$uniq"
    done
}

any_controller_present() {
    local f name uniq
    for f in /sys/bus/hid/devices/*/uevent; do
        [ -e "$f" ] || continue
        name="$(grep -m1 '^HID_NAME=' "$f" 2>/dev/null | cut -d= -f2- || true)"
        uniq="$(grep -m1 '^HID_UNIQ=' "$f" 2>/dev/null | cut -d= -f2- || true)"
        [[ "$name" == *Controller* ]] || [[ "$name" == *Gamepad* ]] || [[ "$name" == *8BitDo* ]] || continue
        [[ "$name" == *LED* ]] && continue
        [[ "$name" == *Light* ]] && continue
        [[ "$name" == *Lighting* ]] && continue
        [ -n "$uniq" ] || continue
        return 0
    done

    compgen -G "/dev/input/js*" >/dev/null && return 0

    local ev
    for ev in /dev/input/event*; do
        [ -e "$ev" ] || continue
        if udevadm info --query=property --name="$ev" 2>/dev/null | grep -q '^ID_INPUT_JOYSTICK=1$'; then
            return 0
        fi
    done

    return 1
}

check_stale_lock() {
    if [ -f "$LOCK" ]; then
        local owner
        owner="$(lock_owner)"
        
        # Case 1: Empty lock file (invalid state)
        if [ -z "$owner" ]; then
            log "lock: stale (empty) -> clearing"
            if ! rm -f "$LOCK" 2>/dev/null; then
                log "lock: WARN: cannot remove empty lock (run: sudo rm -f $LOCK)"
                # Try truncating instead if we can't delete
                : > "$LOCK" 2>/dev/null && rm -f "$LOCK" 2>/dev/null || true
            fi
            return
        fi
        
        # Case 2: Owner device is not present
        if ! id_present "$owner"; then
            log "lock: stale ($owner not present) -> clearing"
            if ! rm -f "$LOCK" 2>/dev/null; then
                log "lock: WARN: cannot remove stale lock (run: sudo rm -f $LOCK)"
            fi
            return
        fi
        
        # Case 3: Lock exists with valid owner that is present - this is fine
        debug "SWITCHER" "lock: valid lock found (owner=$owner)"
    fi
}
