#!/usr/bin/env bash
# audio-control.sh - Audio sink detection and management

resolve_sink_by_alsa() {
    local want_card="${1:-}"
    local want_dev="${2:-}"
    local fallback="${3:-}"
    debug "AUDIO" "resolve_sink_by_alsa: want card=$want_card dev=$want_dev"
    command -v pactl >/dev/null 2>&1 || { debug "AUDIO" "pactl missing, using fallback: $fallback"; echo -n "$fallback"; return 0; }

    local found
    found="$(
        pactl list sinks 2>/dev/null | awk -v want_card="$want_card" -v want_dev="$want_dev" '
            $1=="Sink" && $2 ~ /^#/ { name=""; card=""; dev=""; next }
            $1=="Name:" { name=$2; next }
            $1=="alsa.card" && $2=="=" { gsub(/"/,"",$3); card=$3; next }
            $1=="alsa.device" && $2=="=" { gsub(/"/,"",$3); dev=$3; next }
            name!="" && card==want_card && dev==want_dev { print name; exit 0 }
        '
    )"

    if [ -n "${found:-}" ]; then
        debug "AUDIO" "found sink by alsa: $found"
        echo -n "$found"
    else
        debug "AUDIO" "alsa sink not found, using fallback: $fallback"
        echo -n "$fallback"
    fi
}

couch_sink_name() {
    if [ -n "${COUCH_SINK:-}" ]; then
        echo -n "$COUCH_SINK"
    else
        # Try to find a sink that looks like an HDMI/TV output
        local found
        found="$(pactl list short sinks 2>/dev/null | awk '/hdmi/ || /HDMI/ {print $2}' | head -n 1)"
        if [ -n "$found" ]; then
            debug "AUDIO" "found dynamic hdmi sink: $found"
            echo -n "$found"
        else
            debug "AUDIO" "falling back to configured alsa card/device"
            resolve_sink_by_alsa "$COUCH_ALSA_CARD" "$COUCH_ALSA_DEVICE" ""
        fi
    fi
}

resolve_sink_by_description() {
    local want_desc="${1:-}"
    debug "AUDIO" "resolve_sink_by_description: want '$want_desc'"
    [ -n "$want_desc" ] || return 1
    command -v pactl >/dev/null 2>&1 || return 1

    local found
    found="$(pactl list sinks 2>/dev/null | awk -v want="$want_desc" '
        $1=="Sink" && $2 ~ /^#/ { name=""; desc=""; next }
        $1=="Name:" { name=$2; next }
        $1=="Description:" { desc=$2; for (i=3;i<=NF;i++) desc=desc " " $i; next }
        name!="" && desc==want { print name; exit 0 }
    ' | head -n 1)"
    debug "AUDIO" "description match result: ${found:-NONE}"
    echo -n "$found"
}

resolve_couch_sink_with_wait() {
    local sink i
    debug "AUDIO" "resolve_couch_sink_with_wait: starting retry loop"
    for i in {1..40}; do
        sink="$(couch_sink_name)"
        if [ -n "${sink:-}" ]; then
            debug "AUDIO" "resolved via couch_sink_name (attempt $i)"
            echo -n "$sink"
            return 0
        fi

        sink="$(resolve_sink_by_description "Couch" || true)"
        if [ -n "${sink:-}" ]; then
            debug "AUDIO" "resolved via description 'Couch' (attempt $i)"
            echo -n "$sink"
            return 0
        fi

        [ $((i % 5)) -eq 0 ] && debug "AUDIO" "still waiting for couch sink... (attempt $i)"
        sleep 0.25
    done
    debug "AUDIO" "failed to resolve couch sink after 40 attempts"
    return 1
}

set_default_sink() {
    local sink="${1:-}"
    command -v pactl >/dev/null 2>&1 || return 0
    [ -n "$sink" ] || return 0
    pactl set-default-sink "$sink" >/dev/null 2>&1 || true
    log "audio: default -> $sink"
}

move_all_sink_inputs_to() {
    local sink="${1:-}"
    command -v pactl >/dev/null 2>&1 || return 0
    [ -n "$sink" ] || return 0

    local ids moved=0 id
    ids="$(pactl list short sink-inputs 2>/dev/null | awk '{print $1}' || true)"
    for id in $ids; do
        pactl move-sink-input "$id" "$sink" >/dev/null 2>&1 && moved=$((moved + 1)) || true
    done
    log "audio: moved ${moved} sink-input(s) -> $sink"
}

set_audio_to_sink() {
    local sink="${1:-}"
    set_default_sink "$sink"
    move_all_sink_inputs_to "$sink"
}
