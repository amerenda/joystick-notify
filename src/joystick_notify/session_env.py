"""Ensures the Wayland/D-Bus session environment variables that
`kscreen-doctor` depends on are present, defaulting them the same way v1's
`lib/config-env.sh` did.

Confirmed necessary via live testing 2026-08-21: a systemd `--user`
service (and a plain SSH shell) does not reliably inherit `WAYLAND_DISPLAY`
from the graphical session on this box, even though
`DBUS_SESSION_BUS_ADDRESS` *is* inherited correctly. Without it,
`kscreen-doctor -j` aborts (SIGABRT, exit 134, zero output) rather than
erroring gracefully — which would have silently broken the entire display
detection/switch path (and therefore the wizard's display step) the first
time this ran as an actual systemd service instead of an interactive
shell. `pactl` was confirmed to need no equivalent fix.
"""
from __future__ import annotations

import os


def ensure_session_environment() -> None:
    os.environ.setdefault("XDG_SESSION_TYPE", "wayland")
    os.environ.setdefault("WAYLAND_DISPLAY", "wayland-0")
    os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus")
