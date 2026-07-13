#!/usr/bin/env bash
# force-desk-primary-greeter.sh
# Same enforcement as force-desk-primary.sh, but targets the plasmalogin
# greeter's own KWin/Wayland session instead of a logged-in user session.
#
# Why this exists: force-desk-primary.service (systemd --user,
# After=graphical-session.target) and the 98-monitor-hotplug.rules udev
# trigger both depend on the desk user's own Plasma session being active.
# The plasmalogin greeter runs its own separate KWin-based Wayland
# compositor BEFORE any login, so neither mechanism ever reaches it - both
# HDMI outputs stay extended at the login screen. On this box that trips a
# known amdgpu DCN4 (RDNA4) bug where a CRTC fails to cleanly disable during
# the boot-time multi-monitor modeset (REG_WAIT timeout on
# optc401_disable_crtc in dmesg), which shows up as visible ghosting /
# duplicate-rendered UI on the login screen until the first repaint.
#
# This is installed as a system unit running as the `plasmalogin` user (see
# systemd/plasmalogin-desk-primary.service) so it can legitimately reach
# that user's own session bus - DBus session buses reject cross-uid
# connections even from root, so this has to run as that uid, not as root
# reaching in.
set -euo pipefail

LIB_DIR="/usr/local/lib/joystick-notify"
[ -f "$LIB_DIR/config-env.sh" ] && source "$LIB_DIR/config-env.sh"
DESK_PORT="${DESK_PORT:-HDMI-A-2}"
DESK_MODE="${DESK_MODE:-2560x1440@144}"
COUCH_PORT="${COUCH_PORT:-HDMI-A-1}"

# Wait for kwin_wayland's Wayland socket — NOT just the D-Bus socket.
# The unit fires After=plasmalogin.service, but kwin_wayland in the greeter
# session creates its Wayland socket ~1s later. kscreen-doctor needs the
# Wayland socket (WAYLAND_DISPLAY); it crashes if only D-Bus is up.
xdg="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
wayland_sock=""
for _ in $(seq 1 40); do
    for n in 0 1 2; do
        [ -S "${xdg}/wayland-${n}" ] && { wayland_sock="${xdg}/wayland-${n}"; break 2; }
    done
    sleep 0.5
done

if [ -z "$wayland_sock" ]; then
    echo "force-desk-primary-greeter: Wayland socket never appeared after 20s, skipping" >&2
    exit 0
fi

export WAYLAND_DISPLAY=$(basename "$wayland_sock")
export XDG_RUNTIME_DIR="$xdg"

# Brief pause after socket appears to let kwin_wayland finish initializing
sleep 1

timeout 10 kscreen-doctor \
    "output.${DESK_PORT}.enable" \
    "output.${DESK_PORT}.priority.1" \
    "output.${DESK_PORT}.mode.${DESK_MODE}" \
    "output.${DESK_PORT}.position.0,0" \
    "output.${COUCH_PORT}.disable" 2>/dev/null \
    && echo "force-desk-primary-greeter: desk monitor enforced for greeter session" >&2 \
    || echo "force-desk-primary-greeter: kscreen-doctor failed" >&2
