"""Notification-popup suppression for any active game/stream session --
couch mode (physical controller) and Sunshine's narrow unlock-only path
(Moonlight/MoonDeck/Desktop/Big Picture) alike. Same activate_couch/
activate_desk shape as every other action module (screen_lock.py,
audio.py, display.py, cursor.py).

Confirmed live 2026-09-12: a stock CachyOS update-notifier popup
("Cachy-Update — 4 updates available") interrupted an active MoonDeck
stream, sitting on screen for hours since nothing was there to dismiss
it. Alex's ask was general -- popups must stay hidden for ANY of
couch mode / Sunshine / Moonlight / MoonDeck, not just this one
notifier -- so this suppresses desktop notifications at the D-Bus
level rather than special-casing one app.

Uses the FreeDesktop Notifications spec's Inhibit()/UnInhibit() cookie
pair (org.freedesktop.Notifications), the same "acquire a cookie, hold
it, release it on teardown" idiom screen_lock.py already uses for
org.freedesktop.ScreenSaver.Inhibit(). Confirmed live against this
host's actual notification daemon (KDE Plasma 6.7): the call succeeds
and returns a cookie, and Plasma's own Inhibit() is documented to
suppress the popup bubble specifically (notifications still land in
history) -- exactly "hide popups," not a full notification block. There
is no equivalent of screen_lock's GetActive() to verify against here,
though: Plasma's own `Inhibited` property does NOT reflect an
app-registered inhibit (confirmed live -- it read false immediately
after a successful Inhibit() call, only tracking the manual global "Do
Not Disturb" toggle instead), so Health here can only reflect whether
the D-Bus call itself succeeded, not whether popups are actually being
suppressed. That gap should get a real live-test pass (start a stream,
trigger a notification, confirm no bubble appears) before this is
trusted the way screen_lock's verified unlock is.

qdbus6 can't marshal Inhibit()'s QVariantMap `hints` argument from the
CLI ("Sorry, can't pass arg of type 'QVariantMap'") -- confirmed live.
busctl (part of systemd, always present, unlike the KDE-specific qdbus6)
is used here instead, the one exception to this codebase's usual
qdbus6-for-everything convention.
"""
from __future__ import annotations

import asyncio
import logging

from ..config.schema import NotificationsConfig
from ..health import Health

logger = logging.getLogger(__name__)

RUN_TIMEOUT_S = 5.0

_SERVICE = "org.freedesktop.Notifications"
_PATH = "/org/freedesktop/Notifications"
_INTERFACE = "org.freedesktop.Notifications"


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


def parse_inhibit_cookie(output: str) -> str | None:
    """Parses busctl's `u 1`-shaped stdout for a successful Inhibit()
    call into just the cookie value. Returns None for anything else
    (an error message, empty output, an unexpected type prefix)."""
    parts = output.strip().split()
    if len(parts) == 2 and parts[0] == "u" and parts[1].isdigit():
        return parts[1]
    return None


async def acquire_inhibit(app: str = "joystick-notify", reason: str = "active game/stream session") -> str | None:
    rc, out = await _run([
        "busctl", "--user", "call", _SERVICE, _PATH, _INTERFACE, "Inhibit",
        "ssa{sv}", app, reason, "0",
    ])
    cookie = parse_inhibit_cookie(out) if rc == 0 else None
    if cookie is None:
        logger.warning("notifications: failed to acquire Inhibit() cookie: %s", out.strip())
    return cookie


async def release_inhibit(cookie: str) -> None:
    rc, out = await _run(["busctl", "--user", "call", _SERVICE, _PATH, _INTERFACE, "UnInhibit", "u", cookie])
    if rc != 0:
        logger.warning("notifications: failed to release Inhibit() cookie %s: %s", cookie, out.strip())


async def activate_couch(config: NotificationsConfig, health: Health) -> str | None:
    """Returns the held Inhibit() cookie (if acquired) so the caller can
    release it in activate_desk() -- same "hold a handle, release it on
    teardown" shape as screen_lock.activate_couch()."""
    if not config.enabled:
        health.ok("notifications", "disabled in config")
        return None
    cookie = await acquire_inhibit()
    if cookie is not None:
        health.ok("notifications", "popups suppressed for this session")
    else:
        health.degraded("notifications", "could not acquire Inhibit() cookie -- popups may still appear")
    return cookie


async def activate_desk(config: NotificationsConfig, health: Health, cookie: str | None) -> None:
    if not config.enabled:
        return
    if cookie is not None:
        await release_inhibit(cookie)
    health.ok("notifications", "popup suppression released")
