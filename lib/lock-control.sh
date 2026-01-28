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

any_controller_present() {
    local f name uniq
    for f in /sys/bus/hid/devices/*/uevent; do
        [ -e "$f" ] || continue
        name="$(grep -m1 '^HID_NAME=' "$f" 2>/dev/null | cut -d= -f2- || true)"
        uniq="$(grep -m1 '^HID_UNIQ=' "$f" 2>/dev/null | cut -d= -f2- || true)"
        [[ "$name" == *Controller* ]] || continue
        [[ "$uniq" == *:* ]] || continue
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
        owner="$(lock_owner)"
        if [ -n "$owner" ] && ! id_present "$owner"; then
            log "lock: stale ($owner) -> clearing"
            rm -f "$LOCK"
        fi
    fi
}
