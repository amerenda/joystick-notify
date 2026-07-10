#!/bin/bash
# Sunshine global_prep_cmd "do" — switch to Couch virtual desktop before stream starts.
# Saves the current KDE virtual desktop so sunshine-couch-undo.sh can restore it.
# Steam Big Picture is handled by the app-level launch-bigpicture.sh detached script.
#
# Install: sudo install -Dm0755 sunshine-couch-prep.sh /usr/local/bin/sunshine-couch-prep.sh

export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
export XDG_RUNTIME_DIR=/run/user/1000

# Save current desktop (default to 1 if unavailable)
current=$(qdbus6 org.kde.KWin /KWin org.kde.KWin.currentDesktop 2>/dev/null || echo "")
echo "${current:-1}" > /tmp/sunshine-prev-desktop

# Switch to Couch desktop (2)
qdbus6 org.kde.KWin /KWin org.kde.KWin.setCurrentDesktop 2 >/dev/null 2>&1 || true
