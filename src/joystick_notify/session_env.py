"""Ensures the session environment variables `kscreen-doctor` (and other
display-server-aware tools) depend on are present, defaulting them the
same way v1's `lib/config-env.sh` did -- but v1 only ever ran on one
Wayland/KDE box and hardcoded `XDG_SESSION_TYPE=wayland` unconditionally.
Since this rewrite targets "anyone," not just that one box (see
plans/joystick-notify-v2.md's goals), forcing Wayland on a real X11
session would be wrong: X11 apps need `DISPLAY`, not `WAYLAND_DISPLAY`,
and stamping XDG_SESSION_TYPE=wayland over an actual X11 session could
confuse anything downstream that checks it.

Confirmed necessary via live testing 2026-08-21: a systemd `--user`
service (and a plain SSH shell) does not reliably inherit `WAYLAND_DISPLAY`
from the graphical session on this box, even though
`DBUS_SESSION_BUS_ADDRESS` *is* inherited correctly. Without it,
`kscreen-doctor -j` aborts (SIGABRT, exit 134, zero output) rather than
erroring gracefully. `pactl` was confirmed to need no equivalent fix --
audio, CEC, and controller detection are all display-server-agnostic.
"""
from __future__ import annotations

import os


def _wayland_socket_present() -> bool:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return os.path.exists(os.path.join(runtime_dir, "wayland-0"))


def ensure_session_environment() -> None:
    os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus")

    session_type = os.environ.get("XDG_SESSION_TYPE")
    if not session_type:
        # Infer from what's actually present rather than assuming Wayland
        # unconditionally.
        if os.environ.get("WAYLAND_DISPLAY") or _wayland_socket_present():
            session_type = "wayland"
        elif os.environ.get("DISPLAY"):
            session_type = "x11"
        else:
            session_type = "wayland"  # last-resort default, matches v1's original assumption
        os.environ["XDG_SESSION_TYPE"] = session_type

    if session_type == "wayland":
        os.environ.setdefault("WAYLAND_DISPLAY", "wayland-0")
    elif session_type == "x11":
        os.environ.setdefault("DISPLAY", ":0")
