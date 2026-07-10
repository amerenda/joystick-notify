#!/bin/bash
# Sunshine global_prep_cmd "do" — switch to Couch desktop and launch Steam Big Picture.
# Saves the current KDE virtual desktop so sunshine-couch-undo.sh can restore it.
#
# Install: sudo install -Dm0755 sunshine-couch-prep.sh /usr/local/bin/sunshine-couch-prep.sh

export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
export XDG_RUNTIME_DIR=/run/user/1000
export WAYLAND_DISPLAY=wayland-0
export XDG_SESSION_TYPE=wayland
export SDL_VIDEODRIVER=wayland
export QT_QPA_PLATFORM=wayland
export STEAM_USE_WAYLAND=1

# Save current desktop (default to 1 if unavailable)
current=$(qdbus6 org.kde.KWin /KWin org.kde.KWin.currentDesktop 2>/dev/null || echo "")
echo "${current:-1}" > /tmp/sunshine-prev-desktop

# Switch to Couch desktop (2)
qdbus6 org.kde.KWin /KWin org.kde.KWin.setCurrentDesktop 2 2>/dev/null || true

# Launch Steam Big Picture (fire-and-forget — does not block stream startup)
if pgrep -x steam >/dev/null 2>&1; then
    steam -ifrunning "steam://open/bigpicture" >/dev/null 2>&1 &
else
    steam -gamepadui >/dev/null 2>&1 &
fi
