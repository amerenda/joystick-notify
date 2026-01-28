#!/usr/bin/env bash
# config-env.sh - Global configuration and environment settings

# ===================== CONFIG =====================
DEBUG_MODE="${DEBUG_MODE:-true}"
DEBUG_AUDIO="${DEBUG_AUDIO:-true}"
DEBUG_DISPLAY="${DEBUG_DISPLAY:-false}"
DEBUG_CEC="${DEBUG_CEC:-false}"
DEBUG_PLASMA="${DEBUG_PLASMA:-false}"
DEBUG_SWITCHER="${DEBUG_SWITCHER:-true}"
DEBUG_WRAPPER="${DEBUG_WRAPPER:-true}"

DISCONNECT_GRACE="${DISCONNECT_GRACE:-30}"
STEAM_POLL="${STEAM_POLL:-2}"

# HDMI-CEC
CEC_ENABLED="${CEC_ENABLED:-true}"
CEC_HDMI_PORT="${CEC_HDMI_PORT:-3}"
CEC_COUCH_PORT="${CEC_COUCH_PORT:-$CEC_HDMI_PORT}"
CEC_ADAPTER="${CEC_ADAPTER:-}"
CEC_POWER_OFF_ON_TEARDOWN="${CEC_POWER_OFF_ON_TEARDOWN:-true}"

# KDE Plasma
COUCH_DESKTOP_NAME="${COUCH_DESKTOP_NAME:-Couch}"
COUCH_DESKTOP_NUM="${COUCH_DESKTOP_NUM:-}"
COUCH_ACTIVITY_NAME="${COUCH_ACTIVITY_NAME:-Couch}"
COUCH_ACTIVITY_ID="${COUCH_ACTIVITY_ID:-}"

# Display outputs
DESK_PORT="${DESK_PORT:-HDMI-A-2}"
COUCH_PORT="${COUCH_PORT:-HDMI-A-1}"
FORCE_DESK_PRIMARY="${FORCE_DESK_PRIMARY:-true}"

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

# Paths
LOG=/tmp/joystick-events.log
EVENTS_LOCK=/tmp/joystick-events.lock
LOCK=/tmp/joystick-owner.lock
LAUNCHER="/usr/local/bin/launch-bigpicture.sh"
WATCHLOG=/tmp/joystick-watcher.log
JOY_EVENT="/usr/local/bin/joystick-event.sh"
DESKTOP_STATE="/tmp/joystick-prev-desktop.$(id -u)"
ACTIVITY_STATE="/tmp/joystick-prev-activity.$(id -u)"
CEC_STATE="/tmp/joystick-cec-used.$(id -u)"

# Helpers
have() { command -v "$1" >/dev/null 2>&1; }
is_pid_alive() { local p="${1:-}"; [ -n "$p" ] && kill -0 "$p" >/dev/null 2>&1; }

is_couch_mode() {
    [ -f "$LOCK" ]
}

is_desk_mode() {
    ! is_couch_mode
}
