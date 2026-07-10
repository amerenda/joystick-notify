#!/bin/bash
# Sunshine global_prep_cmd "do" — switch to Couch virtual desktop before stream starts.
# Saves the current KDE virtual desktop so sunshine-couch-undo.sh can restore it.
# Steam Big Picture is handled by the app-level launch-bigpicture.sh detached script,
# but it takes 10-20s to open. This script spawns a background mover that keeps
# pushing any Steam window that appears to desktop 2 for 45s after stream start.
#
# Install: sudo install -Dm0755 sunshine-couch-prep.sh /usr/local/bin/sunshine-couch-prep.sh

export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
export XDG_RUNTIME_DIR=/run/user/1000

# Save current desktop (default to 1 if unavailable)
current=$(qdbus6 org.kde.KWin /KWin org.kde.KWin.currentDesktop 2>/dev/null || echo "")
echo "${current:-1}" > /tmp/sunshine-prev-desktop

# Switch to Couch desktop (2)
qdbus6 org.kde.KWin /KWin org.kde.KWin.setCurrentDesktop 2 >/dev/null 2>&1 || true

# Background: poll for the Steam Big Picture window and move it to desktop 2.
# BP can take 15-25s to appear; KDE restores it to its previous desktop (usually 1).
# Use unique script names per iteration — KWin rejects duplicate names with -1.
(
    mover_pid=$BASHPID
    for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
        sleep 3
        tmpscript=$(mktemp /tmp/kwin-jn-prep-XXXXX.js 2>/dev/null) || break
        cat > "$tmpscript" << 'KWIN_JS'
var wins = workspace.windowList();
var desk2 = workspace.desktops[1];
for (var i = 0; i < wins.length; i++) {
    var w = wins[i];
    if (w.resourceClass === "steam" || w.resourceName === "steam") {
        var desks = w.desktops;
        if (desks.length === 0 || desks[0] !== desk2) {
            w.desktops = [desk2];
        }
    }
}
KWIN_JS
        script_name="jn-sm-${mover_pid}-${i}"
        sid=$(qdbus6 org.kde.KWin /Scripting loadScript "$tmpscript" "$script_name" 2>/dev/null) || { rm -f "$tmpscript"; continue; }
        qdbus6 org.kde.KWin "/Scripting/Script${sid}" run 2>/dev/null || true
        qdbus6 org.kde.KWin "/Scripting/Script${sid}" stop 2>/dev/null || true
        rm -f "$tmpscript" 2>/dev/null || true
    done
) >/dev/null 2>&1 &
