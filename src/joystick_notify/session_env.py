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
import time


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


def wait_for_wayland_socket(timeout: float = 20.0, poll_interval: float = 0.25) -> bool:
    """Poll until the session's real Wayland compositor socket exists on
    disk, instead of trusting a single environment read the way
    `ensure_session_environment()` does.

    KDE's session startup imports the real display-server environment into
    the systemd --user manager as a step that happens *after*
    `graphical-session.target` is already reported active -- so a unit
    that starts as soon as the target activates (any `WantedBy=
    graphical-session.target` unit, e.g. joystick-notify-tray.service) can
    legitimately run before that import lands. When it does,
    `ensure_session_environment()`'s hardcoded fallback ("wayland-0") is a
    guess, not a confirmation, and code that trusts it (PyQt6's
    QApplication) can abort hard against a socket that isn't there yet.
    That race, not a code defect in the Qt init itself, is what froze
    login system-wide on 2026-08-30 (see
    plans/joystick-notify-sunshine-reenable.md's "reboot-and-verify pass"
    incident notes) -- this closes the actual gap instead of the earlier
    disable/re-enable mitigation.

    Re-reads systemd's environment on every attempt (not just once) since
    the import can land at any point during the poll window. Returns True
    the first moment a real socket file is found; False if `timeout`
    elapses first without one appearing. Never raises -- the caller
    decides what to do with a session that never became ready.
    """
    deadline = time.monotonic() + timeout
    while True:
        env = _systemd_user_environment()
        wayland_display = env.get("WAYLAND_DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
        xdg_runtime_dir = env.get("XDG_RUNTIME_DIR") or os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        if wayland_display and os.path.exists(os.path.join(xdg_runtime_dir, wayland_display)):
            os.environ["WAYLAND_DISPLAY"] = wayland_display
            os.environ["XDG_RUNTIME_DIR"] = xdg_runtime_dir
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_interval)
