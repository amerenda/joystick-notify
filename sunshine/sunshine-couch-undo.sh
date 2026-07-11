#!/bin/bash
# Sunshine global_prep_cmd "undo" — restore previous desktop when stream ends.
# Counterpart to sunshine-couch-prep.sh.
#
# Install: sudo install -Dm0755 sunshine-couch-undo.sh /usr/local/bin/sunshine-couch-undo.sh

export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
export XDG_RUNTIME_DIR=/run/user/1000

prev=$(cat /tmp/sunshine-prev-desktop 2>/dev/null || echo "1")
rm -f /tmp/sunshine-prev-desktop
qdbus6 org.kde.KWin /KWin org.kde.KWin.setCurrentDesktop "${prev:-1}" 2>/dev/null || true

# Restore normal screen-lock behavior now that the stream has ended.
/usr/local/bin/couch-mode-screen-unlock.sh stop || true
