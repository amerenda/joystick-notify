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

    # Best-effort: dismiss an already-active lock screen. There is no supported,
    # password-free "unlock" API in modern KDE (removed for security reasons) —
    # this just terminates the greeter UI process if the screen happens to
    # already be locked when a session starts. If the greeter isn't running,
    # this is a no-op.
    pkill -x kscreenlocker_greet 2>/dev/null || true
    ;;
  stop)
    kwriteconfig6 --file kscreenlockerrc --group Daemon --key Autolock true
    ;;
  *)
    echo "Usage: $0 start|stop" >&2
    exit 1
    ;;
esac
