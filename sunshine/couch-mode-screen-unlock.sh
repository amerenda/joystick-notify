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

case "$ACTION" in
  start)
    kwriteconfig6 --file kscreenlockerrc --group Daemon --key Autolock false
    # Signal ksld to reload its config so Autolock=false takes effect immediately.
    qdbus6 org.kde.screensaver /ScreenSaver org.kde.screensaver.configure 2>/dev/null || true

    # Dismiss an already-active lock screen.  On KDE Wayland, ksld holds a
    # compositor-level lock that ONLY ksld itself can release — killing the greeter
    # directly without first signaling ksld causes it to respawn the greeter as a
    # security measure (unexpected child death = re-lock).
    #
    # Two signal paths, both ask ksld to release the lock:
    #   1. SetActive(false) on the session bus → calls requestUnlock() directly on ksld
    #   2. loginctl unlock-session → logind sends Unlock() to ksld via the system bus
    # After 500ms ksld has processed the signals and cleanly killed the greeter.
    # pkill is a last resort only for stuck/zombie greeters that ksld missed.
    qdbus6 org.freedesktop.ScreenSaver /ScreenSaver \
        org.freedesktop.ScreenSaver.SetActive false 2>/dev/null || true
    _seat_session="$(loginctl 2>/dev/null | awk -v uid="$(id -u)" \
        'NR>1 && $2==uid && $4!="" && $4!="-" {print $1; exit}' || true)"
    [ -n "${_seat_session:-}" ] && loginctl unlock-session "$_seat_session" 2>/dev/null || true
    sleep 0.5
    pkill -f kscreenlocker_greet 2>/dev/null || true
    ;;
  stop)
    kwriteconfig6 --file kscreenlockerrc --group Daemon --key Autolock true
    # Signal the running kscreenlocker daemon to reload its config.  Without
    # this, the daemon stays in its disabled state even though we just wrote
    # Autolock=true, and DPMS never re-arms.
    qdbus6 org.kde.screensaver /ScreenSaver org.kde.screensaver.configure 2>/dev/null || true
    # Reset KWin's idle countdown so DPMS starts from "now", not from the last
    # input event (which may have been minutes ago while in couch mode).
    qdbus6 org.freedesktop.ScreenSaver /ScreenSaver \
        org.freedesktop.ScreenSaver.SimulateUserActivity 2>/dev/null || true
    ;;
  *)
    echo "Usage: $0 start|stop" >&2
    exit 1
    ;;
esac
