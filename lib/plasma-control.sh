#!/usr/bin/env bash
# plasma-control.sh - KDE Plasma virtual desktop and activity management

kwin_current_desktop() {
    have qdbus6 || return 1
    qdbus6 org.kde.KWin /KWin org.kde.KWin.currentDesktop 2>/dev/null | head -n 1
}

kwin_set_desktop() {
    local n="${1:-}"
    debug "PLASMA" "kwin_set_desktop: want $n"
    have qdbus6 || return 1
    [[ "$n" =~ ^[0-9]+$ ]] || return 1
    qdbus6 org.kde.KWin /KWin org.kde.KWin.setCurrentDesktop "$n" >/dev/null 2>&1 || return 1
}

kwin_desktop_count() {
    have kreadconfig6 || return 1
    kreadconfig6 --file kwinrc --group Desktops --key Number 2>/dev/null | head -n 1
}

kwin_desktop_name() {
    local n="${1:-}"
    have kreadconfig6 || return 1
    kreadconfig6 --file kwinrc --group Desktops --key "Name_${n}" 2>/dev/null || true
}

kwin_find_desktop_by_name() {
    local want="${1:-}"
    [ -n "$want" ] || return 1
    local count n name
    count="$(kwin_desktop_count || echo)"
    [[ "${count:-}" =~ ^[0-9]+$ ]] || return 1
    for ((n=1; n<=count; n++)); do
        name="$(kwin_desktop_name "$n" | head -n 1)"
        if [ "${name:-}" = "$want" ]; then
            echo -n "$n"
            return 0
        fi
    done
    return 1
}

save_and_switch_to_couch_desktop_best_effort() {
    local cur target count
    cur="$(kwin_current_desktop || echo)"
    [[ "${cur:-}" =~ ^[0-9]+$ ]] || { log "kwin: skip desktop save/switch (no currentDesktop)"; return 0; }
    ( umask 077; echo -n "$cur" >"$DESKTOP_STATE" ) 2>/dev/null || true

    target=""
    if [[ "${COUCH_DESKTOP_NUM:-}" =~ ^[0-9]+$ ]]; then
        target="$COUCH_DESKTOP_NUM"
    else
        target="$(kwin_find_desktop_by_name "$COUCH_DESKTOP_NAME" || true)"
        if [ -z "${target:-}" ]; then
            count="$(kwin_desktop_count || echo)"
            [[ "${count:-}" =~ ^[0-9]+$ ]] && target="$count" || target=""
        fi
    fi

    if [ -z "${target:-}" ]; then
        log "kwin: warn: could not resolve couch desktop (name=$COUCH_DESKTOP_NAME num=${COUCH_DESKTOP_NUM:-auto})"
        return 0
    fi

    if [ "$target" != "$cur" ]; then
        if kwin_set_desktop "$target"; then
            log "kwin: switched desktop $cur -> $target"
        else
            log "kwin: warn: failed to switch desktop $cur -> $target"
        fi
    else
        log "kwin: couch desktop already active (desktop=$cur)"
    fi
}

restore_previous_desktop_best_effort() {
    local prev
    [ -r "$DESKTOP_STATE" ] || return 0
    prev="$(cat "$DESKTOP_STATE" 2>/dev/null || echo)"
    rm -f "$DESKTOP_STATE" >/dev/null 2>&1 || true
    [[ "${prev:-}" =~ ^[0-9]+$ ]] || return 0
    kwin_set_desktop "$prev" && log "kwin: restored desktop -> $prev" || log "kwin: warn: failed to restore desktop -> $prev"
}

activity_current() {
    have qdbus6 || return 1
    qdbus6 org.kde.ActivityManager /ActivityManager/Activities org.kde.ActivityManager.Activities.CurrentActivity 2>/dev/null | head -n 1
}

activity_set_current() {
    local id="${1:-}"
    debug "PLASMA" "activity_set_current: want $id"
    have qdbus6 || return 1
    [ -n "$id" ] || return 1
    qdbus6 org.kde.ActivityManager /ActivityManager/Activities org.kde.ActivityManager.Activities.SetCurrentActivity "$id" >/dev/null 2>&1 || return 1
}

activity_name() {
    local id="${1:-}"
    have qdbus6 || return 1
    [ -n "$id" ] || return 1
    qdbus6 org.kde.ActivityManager /ActivityManager/Activities org.kde.ActivityManager.Activities.ActivityName "$id" 2>/dev/null | head -n 1
}

activity_list() {
    have qdbus6 || return 1
    qdbus6 org.kde.ActivityManager /ActivityManager/Activities org.kde.ActivityManager.Activities.ListActivities 2>/dev/null | tr ' ' '\n' | sed '/^$/d'
}

activity_find_by_name() {
    local want="${1:-}"
    [ -n "$want" ] || return 1
    local id n
    while IFS= read -r id; do
        n="$(activity_name "$id" || true)"
        if [ "${n:-}" = "$want" ]; then
            echo -n "$id"
            return 0
        fi
    done < <(activity_list || true)
    return 1
}

save_and_switch_to_couch_activity_best_effort() {
    local cur target
    cur="$(activity_current || echo)"
    [ -n "${cur:-}" ] || { log "activity: skip (cannot read current activity)"; return 0; }
    ( umask 077; echo -n "$cur" >"$ACTIVITY_STATE" ) 2>/dev/null || true

    target=""
    if [ -n "${COUCH_ACTIVITY_ID:-}" ]; then
        target="$COUCH_ACTIVITY_ID"
    else
        target="$(activity_find_by_name "$COUCH_ACTIVITY_NAME" || true)"
    fi

    if [ -z "${target:-}" ]; then
        log "activity: warn: could not resolve couch activity (name=$COUCH_ACTIVITY_NAME id=${COUCH_ACTIVITY_ID:-auto})"
        return 0
    fi

    if [ "$target" = "$cur" ]; then
        log "activity: couch activity already active"
        return 0
    fi

    if activity_set_current "$target"; then
        log "activity: switched activity -> $COUCH_ACTIVITY_NAME"
    else
        log "activity: warn: failed to switch activity -> $COUCH_ACTIVITY_NAME"
    fi
}

restore_previous_activity_best_effort() {
    local prev
    [ -r "$ACTIVITY_STATE" ] || return 0
    prev="$(cat "$ACTIVITY_STATE" 2>/dev/null || echo)"
    rm -f "$ACTIVITY_STATE" >/dev/null 2>&1 || true
    [ -n "${prev:-}" ] || return 0
    activity_set_current "$prev" && log "activity: restored previous activity" || log "activity: warn: failed to restore previous activity"
}
