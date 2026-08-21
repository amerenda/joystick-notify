"""Ensures the session environment variables that display-server-aware
tools depend on are present, defaulting them the same way v1's
`lib/config-env.sh` did -- but generalized past v1's single Wayland/KDE
box.

Two real bugs found via live testing 2026-08-21 before landing on this
design:

1. `kscreen-doctor -j` aborted (SIGABRT via Qt's qFatal(), confirmed via
   coredumpctl backtrace) with WAYLAND_DISPLAY unset -- a systemd `--user`
   service and an SSH shell don't reliably inherit it.
2. Steam's Big Picture launch failed ("unable to open a connection to X")
   even after fixing (1), because this module originally treated Wayland
   and X11 as mutually exclusive -- inferring "wayland" and therefore
   never setting DISPLAY. Real desktop sessions are frequently hybrid: a
   Wayland compositor (KDE/GNOME) running an XWayland server alongside it
   for X11-only apps, and Steam is a concrete example of an app that needs
   DISPLAY even on an otherwise-Wayland session.

The fix: stop trying to pick one. Default *both* WAYLAND_DISPLAY and
DISPLAY unconditionally (via setdefault, never overriding something
already correctly set). Defaulting the one that's genuinely not in use is
harmless -- an app that doesn't need it won't use it, and an app that
tries to connect to a nonexistent socket/display fails exactly the same
way it would if the variable were simply unset. This is deliberately
"dumb but robust" after two rounds of "clever inference" each introducing
a real bug of its own.
"""
from __future__ import annotations

import os

_KNOWN_SESSION_TYPES = {"wayland", "x11"}


def _wayland_socket_present() -> bool:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return os.path.exists(os.path.join(runtime_dir, "wayland-0"))


def _x11_socket_present() -> bool:
    return os.path.exists("/tmp/.X11-unix/X0")


def ensure_session_environment() -> None:
    os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus")
    os.environ.setdefault("WAYLAND_DISPLAY", "wayland-0")
    os.environ.setdefault("DISPLAY", ":0")

    # Informational only from here down -- nothing in this codebase
    # branches on XDG_SESSION_TYPE; both display variables are already
    # defaulted unconditionally above regardless of what this resolves to.
    if os.environ.get("XDG_SESSION_TYPE") not in _KNOWN_SESSION_TYPES:
        if _wayland_socket_present():
            os.environ["XDG_SESSION_TYPE"] = "wayland"
        elif _x11_socket_present():
            os.environ["XDG_SESSION_TYPE"] = "x11"
