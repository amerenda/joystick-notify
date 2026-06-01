#!/usr/bin/env bash
# config-env.sh - Global configuration and environment settings

# ===================== CONFIG =====================
DEBUG_MODE="${DEBUG_MODE:-false}"
DEBUG_AUDIO="${DEBUG_AUDIO:-false}"
DEBUG_BLUETOOTH="${DEBUG_BLUETOOTH:-false}"
DEBUG_DISPLAY="${DEBUG_DISPLAY:-false}"
DEBUG_CEC="${DEBUG_CEC:-false}"
DEBUG_PLASMA="${DEBUG_PLASMA:-false}"
DEBUG_SWITCHER="${DEBUG_SWITCHER:-false}"
DEBUG_WRAPPER="${DEBUG_WRAPPER:-false}"

DISCONNECT_GRACE="${DISCONNECT_GRACE:-30}"
STEAM_POLL="${STEAM_POLL:-2}"

# HDMI-CEC
CEC_ENABLED="${CEC_ENABLED:-true}"
CEC_HDMI_PORT="${CEC_HDMI_PORT:-3}"
CEC_COUCH_PORT="${CEC_COUCH_PORT:-$CEC_HDMI_PORT}"
CEC_ADAPTER="${CEC_ADAPTER:-}"
# Seconds to wait after Image View On before sending Active Source (e.g. 1-2 for receiver chains)
CEC_WAKE_DELAY="${CEC_WAKE_DELAY:-0}"
# Override physical address for Set Stream Path / Active Source (e.g. 2.0.0.0 = receiver HDMI 2). When set, used instead of discovered address so the receiver switches to the correct input even if the CEC dongle is on a different port.
CEC_ACTIVE_SOURCE_PHYS_ADDR="${CEC_ACTIVE_SOURCE_PHYS_ADDR:-}"
CEC_POWER_OFF_ON_TEARDOWN="${CEC_POWER_OFF_ON_TEARDOWN:-true}"

# KDE Plasma
COUCH_DESKTOP_NAME="${COUCH_DESKTOP_NAME:-Couch}"
COUCH_DESKTOP_NUM="${COUCH_DESKTOP_NUM:-}"
COUCH_ACTIVITY_NAME="${COUCH_ACTIVITY_NAME:-Couch}"
COUCH_ACTIVITY_ID="${COUCH_ACTIVITY_ID:-}"

# Display outputs
DESK_PORT="${DESK_PORT:-HDMI-A-2}"
COUCH_PORT="${COUCH_PORT:-HDMI-A-1}"
FORCE_DESK_PRIMARY="${FORCE_DESK_PRIMARY:-false}"

# Display modes
DESK_MODE="${DESK_MODE:-2560x1440@144}"
COUCH_MODE="${COUCH_MODE:-3840x2160@60}"

# Audio
HEADSET_SINK="${HEADSET_SINK:-alsa_output.usb-SteelSeries_Arctis_Nova_7X-00.iec958-stereo}"
COUCH_ALSA_CARD="${COUCH_ALSA_CARD:-2}"
COUCH_ALSA_DEVICE="${COUCH_ALSA_DEVICE:-9}"
COUCH_SINK="${COUCH_SINK:-}"

# Wayland/KDE session env
export XDG_SESSION_TYPE=wayland
export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"

# Base directories
JN_BASE=/tmp/joystick-notify
JN_LOGS="$JN_BASE/logs"
JN_LOCKS="$JN_BASE/locks"

# Paths - Logs
LOG="$JN_LOGS/events.log"
WATCHLOG="$JN_LOGS/watcher.log"
BT_DEBUG_LOG="$JN_LOGS/bluetooth-events.log"

# Paths - Locks and state
EVENTS_LOCK="$JN_LOCKS/events.lock"
LOCK="$JN_LOCKS/owner.lock"
MANUAL_LOCK="$JN_LOCKS/manual.lock"
DESKTOP_STATE="$JN_LOCKS/prev-desktop.$(id -u)"
ACTIVITY_STATE="$JN_LOCKS/prev-activity.$(id -u)"
CEC_STATE="$JN_LOCKS/cec-used.$(id -u)"

# Paths - Scripts
LAUNCHER="/usr/local/bin/launch-bigpicture.sh"
JOY_EVENT="/usr/local/bin/joystick-event.sh"

# Helpers
have() { command -v "$1" >/dev/null 2>&1; }
is_pid_alive() { local p="${1:-}"; [ -n "$p" ] && kill -0 "$p" >/dev/null 2>&1; }

is_couch_mode() {
    [ -f "$LOCK" ]
}

is_desk_mode() {
    ! is_couch_mode
}

is_manual_couch() {
    [ -f "$MANUAL_LOCK" ] && [ "$(cat "$MANUAL_LOCK" 2>/dev/null)" = "couch" ]
}

is_manual_desk() {
    [ -f "$MANUAL_LOCK" ] && [ "$(cat "$MANUAL_LOCK" 2>/dev/null)" = "desk" ]
}

is_manual_mode() {
    [ -f "$MANUAL_LOCK" ]
}

# Ensure directories exist with proper permissions
ensure_jn_dirs() {
    if [ ! -d "$JN_BASE" ]; then
        mkdir -p "$JN_BASE" 2>/dev/null || true
        chmod 777 "$JN_BASE" 2>/dev/null || true
    fi
    if [ ! -d "$JN_LOGS" ]; then
        mkdir -p "$JN_LOGS" 2>/dev/null || true
        chmod 777 "$JN_LOGS" 2>/dev/null || true
    fi
    if [ ! -d "$JN_LOCKS" ]; then
        mkdir -p "$JN_LOCKS" 2>/dev/null || true
        chmod 777 "$JN_LOCKS" 2>/dev/null || true
    fi
}
