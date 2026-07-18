#!/usr/bin/env bash
# screen-lock-inhibit-daemon.sh
# Registers a ScreenSaver.Inhibit() cookie on the KDE session bus and holds it
# for as long as this process is running.  Kill the process (SIGTERM) to release
# the inhibit and re-arm auto-locking.
#
# Why this exists: kwriteconfig6 + qdbus6 configure disables Autolock for future
# idle-timer fires, but a process-held Inhibit() token is honored even if ksld
# reloads its config, receives spurious lock requests, or the monitor blanks via
# DPMS.  This is the correct mechanism for applications that need to prevent the
# screen from locking while they have an active session.
#
# Usage: screen-lock-inhibit-daemon.sh "App Name" "Reason"
# Install: sudo install -Dm0755 screen-lock-inhibit-daemon.sh \
#            /usr/local/bin/screen-lock-inhibit-daemon.sh

set -euo pipefail

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=${XDG_RUNTIME_DIR}/bus}"

APP="${1:-sunshine}"
REASON="${2:-active streaming session}"

COOKIE=$(qdbus6 org.freedesktop.ScreenSaver /ScreenSaver \
    org.freedesktop.ScreenSaver.Inhibit "$APP" "$REASON" 2>/dev/null || echo "")

cleanup() {
    if [ -n "${COOKIE:-}" ]; then
        qdbus6 org.freedesktop.ScreenSaver /ScreenSaver \
            org.freedesktop.ScreenSaver.UnInhibit "$COOKIE" 2>/dev/null || true
    fi
    exit 0
}
trap cleanup TERM INT HUP

# Stay alive until killed.  The inhibit is active as long as this process holds
# the open D-Bus connection that owns the cookie.
while true; do
    sleep 30
done
