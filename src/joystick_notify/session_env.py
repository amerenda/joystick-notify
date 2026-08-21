"""Ensures the session environment variables that display-server-aware
tools depend on are present, replacing three successive rounds of
individually-guessed defaults with one authoritative source.

The history here matters, because it's the reason this module looks the
way it does. Live testing 2026-08-21 found, one at a time, in this order:

1. `kscreen-doctor -j` aborted (SIGABRT via Qt's qFatal(), confirmed via
   coredumpctl) with WAYLAND_DISPLAY unset.
2. Steam's Big Picture launch failed ("unable to open a connection to X")
   because a first fix defaulted only WAYLAND_DISPLAY, never DISPLAY.
3. Steam failed *again*, same error message, after DISPLAY was also
   defaulted — this time because of a missing XAUTHORITY (X11 refuses an
   unauthenticated connection even with a valid DISPLAY).

That's the exact reactive, one-variable-at-a-time pattern this project
exists to avoid. The actual root cause was never "which variable is
missing this time" — it was that every test ran as `nohup jn-daemon ... &`
from an SSH shell, which structurally cannot have the real session
environment, because SSH doesn't go through the desktop session startup
that populates it. `systemctl --user show-environment` already has the
complete, correct set (confirmed live: DISPLAY, WAYLAND_DISPLAY,
XAUTHORITY, XDG_SESSION_TYPE, all correct) — KDE's session startup
populates systemd's user manager instance specifically so real
`systemd --user` services never have to guess. A real deployment (systemd
unit under graphical-session.target) would have had all of this from the
very first test.

This module now pulls that complete environment wholesale via
`setdefault` (never overriding anything already correctly set) rather
than enumerating which specific variables matter — closing the whole
class of "some app needs env var X nobody's discovered needing yet," not
just the three hit so far. The three original per-variable guesses remain
only as a last-resort fallback for when systemd's environment genuinely
isn't available (non-systemd systems, or a session not yet imported).
"""
from __future__ import annotations

import os
import subprocess


def parse_systemd_environment(output: str) -> dict[str, str]:
    """Pure parsing of `systemctl --user show-environment`'s KEY=VALUE
    lines — split out so this is testable against real captured output
    without needing a real systemd user session.
    """
    env: dict[str, str] = {}
    for line in output.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key:
            env[key] = value
    return env


def _systemd_user_environment() -> dict[str, str]:
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show-environment"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return {}
    if result.returncode != 0:
        return {}
    return parse_systemd_environment(result.stdout)


def ensure_session_environment() -> None:
    for key, value in _systemd_user_environment().items():
        os.environ.setdefault(key, value)

    # Last-resort fallback only for what systemd's environment didn't
    # provide (non-systemd systems, or a session not yet fully imported).
    os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus")
    os.environ.setdefault("WAYLAND_DISPLAY", "wayland-0")
    os.environ.setdefault("DISPLAY", ":0")
