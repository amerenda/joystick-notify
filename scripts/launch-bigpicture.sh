#!/usr/bin/env bash
# launch-bigpicture.sh
set -euo pipefail

# ---- Cursor hiding (KDE Plasma / KWin, Wayland) ----
# We enable KWin's built-in "Hide Cursor" effect while couch-mode is active, and restore
# the user's previous settings when we exit.
#
# NOTE: Because this script is launched in the background by monitor-switcher, we keep it
# running until couch-mode ends (lock file removed) so the cursor stays hidden.

HIDE_CURSOR="${HIDE_CURSOR:-1}"
HIDE_CURSOR_TIMEOUT="${HIDE_CURSOR_TIMEOUT:-1}"

# Base directories - must match lib/config-env.sh
JN_LOCKS="/tmp/joystick-notify/locks"
mkdir -p "$JN_LOCKS" 2>/dev/null || true

LOCKFILE="${LOCKFILE:-$JN_LOCKS/launch-bigpicture.lock}"
STATE_FILE="$JN_LOCKS/launch-bigpicture-state.$(id -u)"

have() { command -v "$1" >/dev/null 2>&1; }

save_hidecursor_state_best_effort() {
  have kreadconfig6 || return 0

  # Save a small set of keys we might touch. Group naming can vary by KWin version,
  # so we probe common candidates for the effect group.
  local prev_enabled prev_hideontyping prev_inact grp found_grp
  prev_enabled="$(kreadconfig6 --file kwinrc --group Plugins --key hidecursorEnabled 2>/dev/null || true)"

  found_grp=""
  for grp in "Effect-hidecursor" "Effect-HideCursor" "HideCursor" "HideCursorEffect"; do
    prev_hideontyping="$(kreadconfig6 --file kwinrc --group "$grp" --key HideOnTyping 2>/dev/null || true)"
    prev_inact="$(kreadconfig6 --file kwinrc --group "$grp" --key InactivityDuration 2>/dev/null || true)"
    if [ -n "${prev_hideontyping:-}" ] || [ -n "${prev_inact:-}" ]; then
      found_grp="$grp"
      break
    fi
  done

  {
    echo "PREV_ENABLED=${prev_enabled:-}"
    echo "PREV_GRP=${found_grp:-}"
    echo "PREV_HIDE_ON_TYPING=${prev_hideontyping:-}"
    echo "PREV_INACTIVITY_DURATION=${prev_inact:-}"
  } >"$STATE_FILE" 2>/dev/null || true
}

enable_hidecursor_best_effort() {
  [ "$HIDE_CURSOR" != "0" ] || return 0
  have kwriteconfig6 || return 0
  have qdbus6 || return 0

  save_hidecursor_state_best_effort

  kwriteconfig6 --file kwinrc --group Plugins --key hidecursorEnabled true >/dev/null 2>&1 || true
  for grp in "Effect-hidecursor" "Effect-HideCursor" "HideCursor" "HideCursorEffect"; do
    kwriteconfig6 --file kwinrc --group "$grp" --key HideOnTyping true >/dev/null 2>&1 || true
    kwriteconfig6 --file kwinrc --group "$grp" --key InactivityDuration "$HIDE_CURSOR_TIMEOUT" >/dev/null 2>&1 || true
  done

  qdbus6 org.kde.KWin /Effects org.kde.kwin.Effects.loadEffect hidecursor >/dev/null 2>&1 || true
  qdbus6 org.kde.KWin /Effects org.kde.kwin.Effects.reconfigureEffect hidecursor >/dev/null 2>&1 || true
  qdbus6 org.kde.KWin /KWin org.kde.KWin.reconfigure >/dev/null 2>&1 || true
}

restore_hidecursor_best_effort() {
  [ "$HIDE_CURSOR" != "0" ] || return 0
  have kwriteconfig6 || return 0
  have qdbus6 || return 0

  # If we couldn't save state, best-effort just disable the effect plugin.
  if [ ! -r "$STATE_FILE" ]; then
    kwriteconfig6 --file kwinrc --group Plugins --key hidecursorEnabled false >/dev/null 2>&1 || true
    qdbus6 org.kde.KWin /Effects org.kde.kwin.Effects.unloadEffect hidecursor >/dev/null 2>&1 || true
    qdbus6 org.kde.KWin /KWin org.kde.KWin.reconfigure >/dev/null 2>&1 || true
    return 0
  fi

  # shellcheck disable=SC1090
  source "$STATE_FILE" 2>/dev/null || true

  # Restore plugin enabled state if we have it.
  if [ -n "${PREV_ENABLED:-}" ]; then
    kwriteconfig6 --file kwinrc --group Plugins --key hidecursorEnabled "$PREV_ENABLED" >/dev/null 2>&1 || true
  else
    kwriteconfig6 --file kwinrc --group Plugins --key hidecursorEnabled false >/dev/null 2>&1 || true
  fi

  # Restore effect config if we have a group.
  if [ -n "${PREV_GRP:-}" ]; then
    if [ -n "${PREV_HIDE_ON_TYPING:-}" ]; then
      kwriteconfig6 --file kwinrc --group "$PREV_GRP" --key HideOnTyping "$PREV_HIDE_ON_TYPING" >/dev/null 2>&1 || true
    fi
    if [ -n "${PREV_INACTIVITY_DURATION:-}" ]; then
      kwriteconfig6 --file kwinrc --group "$PREV_GRP" --key InactivityDuration "$PREV_INACTIVITY_DURATION" >/dev/null 2>&1 || true
    fi
  fi

  rm -f "$STATE_FILE" >/dev/null 2>&1 || true

  qdbus6 org.kde.KWin /Effects org.kde.kwin.Effects.reconfigureEffect hidecursor >/dev/null 2>&1 || true
  qdbus6 org.kde.KWin /Effects org.kde.kwin.Effects.unloadEffect hidecursor >/dev/null 2>&1 || true
  qdbus6 org.kde.KWin /KWin org.kde.KWin.reconfigure >/dev/null 2>&1 || true
}

trap restore_hidecursor_best_effort EXIT

# ---- Environment (Wayland / KDE Plasma) ----
# Source modular libraries so we can use debug/log
LIB_DIR="/usr/local/lib/joystick-notify"
for lib in "$LIB_DIR"/*.sh; do
    # shellcheck disable=SC1090
    [ -f "$lib" ] && source "$lib"
done

export XDG_SESSION_TYPE=wayland
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus"
export SDL_VIDEODRIVER=wayland
export QT_QPA_PLATFORM=wayland
export STEAM_USE_WAYLAND=1
export STEAM_COMPAT_COMMAND_PREFIX="/usr/local/bin/game-wrapper.sh"

# Always log the prefix setting to verify it's happening
log "steam: environment: STEAM_COMPAT_COMMAND_PREFIX=$STEAM_COMPAT_COMMAND_PREFIX"
debug "SWITCHER" "Full environment for Steam prepared."
debug "SWITCHER" "Current environment STEAM vars: $(env | grep STEAM || true)"

# ---- Helpers ----
is_steam_running() {
  pgrep -x steam >/dev/null 2>&1 || pgrep -f '/steam' >/dev/null 2>&1
}

# Always prefer steam:// so an existing client can be reused
open_bigpicture() {
  # -ifrunning avoids spawning a second instance
  steam -ifrunning "steam://open/bigpicture" >/dev/null 2>&1 || \
  steam "steam://open/bigpicture" >/dev/null 2>&1
}

# ---- Main ----
main() {
  enable_hidecursor_best_effort

  if is_steam_running; then
    # Steam already running → just switch it into Big Picture
    open_bigpicture &
  else
    # Steam not running → start directly into Big Picture
    steam -gamepadui &
  fi

  # Keep running until couch-mode ends so cursor hiding remains in effect.
  while [ -e "$LOCKFILE" ]; do
    sleep 1
  done
}

main "$@"



