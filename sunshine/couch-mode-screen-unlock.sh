#!/usr/bin/env bash
# Shared by joystick-notify (local couch mode, controller-triggered) and Sunshine
# (remote streaming session) so a gaming session never gets interrupted by KDE's
# screen lock, and doesn't require typing a password to start playing.
#
# Deliberate security tradeoff, accepted by the user: this machine is treated as
# physically secure (single-user household), so convenience wins over lock-screen
# protection during active gaming/streaming sessions. SSH remains the actual
# security boundary for this host.
#
# Usage: couch-mode-screen-unlock.sh start|stop
#   start - disable KDE's idle autolock for the duration of the session, and
#           best-effort dismiss an already-active lock screen
#   stop  - restore KDE's idle autolock when the session ends
#
# Install: sudo install -Dm0755 couch-mode-screen-unlock.sh \
#            /usr/local/bin/couch-mode-screen-unlock.sh

set -euo pipefail

ACTION="${1:?Usage: $0 start|stop}"

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=${XDG_RUNTIME_DIR}/bus}"

LOG=/tmp/couch-unlock-debug.log

_log() { printf '[%s] couch-unlock %s: %s\n' "$(date '+%T')" "$ACTION" "$*" >> "$LOG" 2>/dev/null || true; }

case "$ACTION" in
  start)
    _log "begin (DBUS=$DBUS_SESSION_BUS_ADDRESS)"
    kwriteconfig6 --file kscreenlockerrc --group Daemon --key Autolock false
    _log "kwriteconfig6 Autolock=false: $?"

    # Tell ksld to reload its config so Autolock=false takes effect immediately.
    qdbus6 org.kde.screensaver /ScreenSaver org.kde.screensaver.configure 2>/dev/null; _rc=$? || true
    _log "qdbus6 configure: $_rc"

    # Dismiss an already-active lock screen.  On KDE Wayland, ksld holds a
    # compositor-level lock that ONLY ksld itself can release — killing the greeter
    # directly without first signaling ksld causes it to respawn the greeter as a
    # security measure (unexpected child death = re-lock).
    #
    # Two signal paths (belt + suspenders), both ask ksld to release the lock:
    #   1. SetActive(false) on the session bus → calls requestUnlock() directly on ksld
    #   2. loginctl unlock-session → logind sends Unlock() to ksld via the system bus
    # After 500ms ksld has processed the signals and cleanly killed the greeter.
    # pkill is a last resort only for stuck/zombie greeters that ksld missed.
    qdbus6 org.freedesktop.ScreenSaver /ScreenSaver \
        org.freedesktop.ScreenSaver.SetActive false 2>/dev/null; _rc=$? || true
    _log "SetActive(false): $_rc"

    _seat_session="$(loginctl 2>/dev/null | awk -v uid="$(id -u)" \
        'NR>1 && $2==uid && $4!="" && $4!="-" {print $1; exit}' || true)"
    _log "seat session: '${_seat_session:-}'"
    if [ -n "${_seat_session:-}" ]; then
        loginctl unlock-session "$_seat_session" 2>/dev/null; _rc=$? || true
        _log "loginctl unlock-session: $_rc"
    else
        _log "loginctl unlock-session: (no session)"
    fi

    # Give ksld 500ms to process the unlock signals and cleanly dismiss the greeter.
    # Killing the greeter immediately is a race: if ksld hasn't processed the signal
    # yet it treats the unexpected greeter death as a crash and re-locks the screen.
    sleep 0.5
    pkill -f kscreenlocker_greet 2>/dev/null; _rc=$? || true
    _log "pkill: $_rc (last resort)"

    # Reset idle counter so auto-lock timer starts from now (not from last local input).
    qdbus6 org.freedesktop.ScreenSaver /ScreenSaver \
        org.freedesktop.ScreenSaver.SimulateUserActivity 2>/dev/null; _rc=$? || true
    _log "SimulateUserActivity: $_rc"

    _log "done. GetActive=$(qdbus6 org.freedesktop.ScreenSaver /ScreenSaver org.freedesktop.ScreenSaver.GetActive 2>/dev/null || echo ?)"
    ;;

  stop)
    _log "begin"
    kwriteconfig6 --file kscreenlockerrc --group Daemon --key Autolock true
    _log "kwriteconfig6 Autolock=true: $?"

    # Reset the idle counter BEFORE reloading ksld's config.  If we configure()
    # first, ksld re-arms the lock timer using the CURRENT idle time — which may
    # be hours (no local input during a streaming session) — and immediately locks.
    # SimulateUserActivity resets that counter to zero first so the newly-armed
    # timer starts a fresh countdown.
    qdbus6 org.freedesktop.ScreenSaver /ScreenSaver \
        org.freedesktop.ScreenSaver.SimulateUserActivity 2>/dev/null; _rc=$? || true
    _log "SimulateUserActivity (before configure): $_rc"

    qdbus6 org.kde.screensaver /ScreenSaver org.kde.screensaver.configure 2>/dev/null; _rc=$? || true
    _log "qdbus6 configure: $_rc"
    _log "done"
    ;;

  *)
    echo "Usage: $0 start|stop" >&2
    exit 1
    ;;
esac
