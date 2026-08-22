"""Screen-lock bypass for couch mode — ports v1's sunshine/couch-mode-
screen-unlock.sh + screen-lock-inhibit-daemon.sh, generalized past their
Sunshine-specific origins to the same activate_couch/activate_desk shape
every other action module uses (display.py, audio.py, cec_control.py).

Two layers, same as v1:
1. Config-based: disable KDE's idle Autolock, and if the screen is
   already locked, dismiss it via a verified sequence (SetActive(false) +
   loginctl unlock-session as belt-and-suspenders, a brief wait for ksld
   to process it, pkill on the greeter only as a last-resort for stuck
   cases) — then verify GetActive() actually became false rather than
   trusting the signals fired cleanly, matching every other action
   module's verify-don't-trust discipline.
2. A held `ScreenSaver.Inhibit()` D-Bus cookie for the whole couch-mode
   session — the robust layer for cases where the config-based disable
   alone doesn't hold (ksld reloading, spurious lock requests). v1 held
   this open by spawning a whole separate detached process whose only job
   was keeping a D-Bus connection alive, with a PID file for cleanup --
   v2 doesn't need that: the daemon is already a persistent process, so
   the cookie is just held in memory here and released directly on
   teardown, no second process required.

This is a real security tradeoff, not a cosmetic feature — bypassing the
lock screen deliberately makes physical access to the machine equivalent
to being logged in. v1's author accepted this explicitly for a single-
user, physically-secure household. It must stay an explicit opt-in here
(config.screen_lock.enabled, default False), not a silent default for
"anyone" installing this tool.
"""
from __future__ import annotations

import asyncio
import logging

from ..config.schema import ScreenLockConfig
from ..health import Health

logger = logging.getLogger(__name__)

RUN_TIMEOUT_S = 5.0


async def _run(cmd: list[str], timeout: float = RUN_TIMEOUT_S) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
    except FileNotFoundError as e:
        return -1, str(e)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, "timeout"
    return proc.returncode, out.decode(errors="replace")


def parse_seat_session(loginctl_output: str, uid: str) -> str | None:
    """Finds the graphical seat session for `uid` from `loginctl`'s table
    output — looks up columns by header name rather than hardcoded
    position, so this doesn't silently break if the column order or set
    ever changes. A session only counts if it has a real SEAT (not "-") --
    matches v1's own filter for exactly this reason: SSH-spawned session
    scopes and other seatless sessions must not be mistaken for the
    graphical one.
    """
    lines = loginctl_output.strip().splitlines()
    if not lines:
        return None
    header = lines[0].split()
    try:
        uid_idx = header.index("UID")
        seat_idx = header.index("SEAT")
        session_idx = header.index("SESSION")
    except ValueError:
        return None
    for line in lines[1:]:
        parts = line.split()
        if len(parts) <= max(uid_idx, seat_idx, session_idx):
            continue
        if parts[uid_idx] == uid and parts[seat_idx] not in ("", "-"):
            return parts[session_idx]
    return None


def parse_dbus_bool(output: str) -> bool:
    return output.strip().lower() == "true"


async def get_active() -> bool:
    rc, out = await _run(["qdbus6", "org.freedesktop.ScreenSaver", "/ScreenSaver", "org.freedesktop.ScreenSaver.GetActive"])
    return rc == 0 and parse_dbus_bool(out)


async def _find_seat_session() -> str | None:
    import os

    rc, out = await _run(["loginctl"])
    if rc != 0:
        return None
    return parse_seat_session(out, str(os.getuid()))


async def simulate_user_activity() -> None:
    await _run(["qdbus6", "org.freedesktop.ScreenSaver", "/ScreenSaver", "org.freedesktop.ScreenSaver.SimulateUserActivity"])


async def activate_screensaver(health: Health) -> bool:
    """Explicitly engages the screensaver (SetActive(true)) for couch-idle
    (controller disconnected, launched game still running) -- couch mode
    stays fully set up (display/audio untouched, Mode.COUCH unchanged) so
    a reconnect resumes instantly; this just blanks the screen in the
    meantime. Verified via GetActive() rather than trusted blind, matching
    every other action module's discipline here.

    Note: an explicit SetActive(true) is expected to work regardless of
    whether a ScreenSaver.Inhibit() cookie is currently held for the whole
    couch session (screen_lock.enabled + hold_inhibit) -- Inhibit() is
    documented to suppress automatic *idle-timer* activation specifically,
    not a direct manual request. Not yet confirmed against real KDE
    behavior with both features enabled simultaneously; worth checking
    during dogfooding.
    """
    await _run(["qdbus6", "org.freedesktop.ScreenSaver", "/ScreenSaver", "org.freedesktop.ScreenSaver.SetActive", "true"])
    active = await get_active()
    if active:
        health.ok("screensaver", "engaged for couch idle")
    else:
        health.degraded("screensaver", "SetActive(true) did not take")
    return active


async def deactivate_screensaver(health: Health) -> None:
    await _run(["qdbus6", "org.freedesktop.ScreenSaver", "/ScreenSaver", "org.freedesktop.ScreenSaver.SetActive", "false"])
    await simulate_user_activity()
    health.ok("screensaver", "dismissed")


async def unlock_and_disable_autolock(health: Health, *, verify_attempts: int = 3, verify_delay_s: float = 0.5) -> None:
    await _run(["kwriteconfig6", "--file", "kscreenlockerrc", "--group", "Daemon", "--key", "Autolock", "false"])
    await _run(["qdbus6", "org.kde.screensaver", "/ScreenSaver", "org.kde.screensaver.configure"])

    await _run(["qdbus6", "org.freedesktop.ScreenSaver", "/ScreenSaver", "org.freedesktop.ScreenSaver.SetActive", "false"])
    session = await _find_seat_session()
    if session is not None:
        await _run(["loginctl", "unlock-session", session])
    else:
        logger.warning("screen_lock: no seat session found, relying on SetActive(false) alone")

    for attempt in range(1, verify_attempts + 1):
        await asyncio.sleep(verify_delay_s)
        if not await get_active():
            logger.info("screen_lock: unlocked and confirmed (attempt %d/%d)", attempt, verify_attempts)
            break
        logger.warning("screen_lock: still locked after unlock signals (attempt %d/%d)", attempt, verify_attempts)
    else:
        # Last resort: v1's own comment is explicit that this is a stuck/
        # zombie-greeter fallback, not the primary mechanism -- killing it
        # without ksld having processed an unlock first can cause ksld to
        # treat the death as a crash and immediately re-lock.
        await _run(["pkill", "-f", "kscreenlocker_greet"])
        await asyncio.sleep(verify_delay_s)

    still_locked = await get_active()
    await simulate_user_activity()
    if still_locked:
        health.failed("screen_lock", "could not dismiss active lock screen")
    else:
        health.ok("screen_lock", "unlocked, autolock disabled for this session")


async def restore_autolock(health: Health) -> None:
    # Reset the idle counter BEFORE re-enabling Autolock: reload the config
    # first and ksld re-arms using the CURRENT idle time (which may be
    # hours during a long couch session with no local keyboard/mouse
    # input), locking again immediately. Order matters here.
    await simulate_user_activity()
    await _run(["kwriteconfig6", "--file", "kscreenlockerrc", "--group", "Daemon", "--key", "Autolock", "true"])
    await _run(["qdbus6", "org.kde.screensaver", "/ScreenSaver", "org.kde.screensaver.configure"])
    health.ok("screen_lock", "autolock restored")


async def acquire_inhibit(app: str = "joystick-notify", reason: str = "active couch session") -> str | None:
    rc, out = await _run(
        ["qdbus6", "org.freedesktop.ScreenSaver", "/ScreenSaver", "org.freedesktop.ScreenSaver.Inhibit", app, reason]
    )
    cookie = out.strip()
    if rc != 0 or not cookie:
        logger.warning("screen_lock: failed to acquire Inhibit() cookie: %s", out.strip())
        return None
    return cookie


async def release_inhibit(cookie: str) -> None:
    await _run(["qdbus6", "org.freedesktop.ScreenSaver", "/ScreenSaver", "org.freedesktop.ScreenSaver.UnInhibit", cookie])


async def activate_couch(config: ScreenLockConfig, health: Health) -> str | None:
    """Returns the held Inhibit() cookie (if acquired) so the caller can
    release it in activate_desk() — same "hold a handle, release it on
    teardown" shape as cec_control.wake_and_select_input's retry task.
    """
    if not config.enabled:
        health.ok("screen_lock", "disabled in config")
        return None
    await unlock_and_disable_autolock(health)
    if config.hold_inhibit:
        return await acquire_inhibit()
    return None


async def activate_desk(config: ScreenLockConfig, health: Health, cookie: str | None) -> None:
    if not config.enabled:
        return
    if cookie is not None:
        await release_inhibit(cookie)
    await restore_autolock(health)
