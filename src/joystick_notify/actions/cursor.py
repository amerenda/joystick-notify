"""Couch-mode mouse cursor hiding — hooks into the same activate_couch/
activate_desk points every other action module (display.py, audio.py,
screen_lock.py) uses.

KDE's own cursor-hide is idle-timer based: whatever wakes it -- a real
mouse move, or in couch mode's case, whatever the controller ends up
generating -- makes the real cursor reappear a few minutes later,
regardless of mode. Rather than depend on (or fight) that timer, this
switches the whole cursor theme to a fully transparent one (built and
installed on the host by ansible-playbooks' roles/mouse-hide, PR #76) --
with no visible pixels in the theme at all, it doesn't matter what wakes
the cursor, there's nothing to show.

`kapplymousetheme` -- KDE's normal live-theme-switch tool -- refuses to
run at all under Wayland: it hard-checks KWindowSystem::isPlatformX11()
and exits (confirmed live 2026-08-29). `plasma-apply-cursortheme` is the
Wayland-capable equivalent, so this uses it, plus a hand-written
~/.icons/default/index.theme for GTK/SDL apps that resolve "the cursor
theme" via the classic Xcursor "default" convention (Steam's own UI).

Applying is VERIFIED, not assumed (2026-09-26 incident: desk restore
reported OK while KWin's live theme stayed "invisible" for 10+ hours, so
the mouse stayed hidden in desk mode). The earlier kwriteconfig6 +
`qdbus6 org.kde.KWin /KWin reconfigure` path wrote the right files but
did not make KWin reload the theme. And plasma-apply-cursortheme itself
compares the requested theme against kcminputrc, not against KWin's live
state, so once the files and the compositor drift apart it prints "already
set" and does nothing. So after applying, KWin's live `themeName`
(supportInformation) is read back; on mismatch the theme is forced by
applying a different theme first and then the target again.

Best-effort, like audio.py -- a stuck cursor theme is annoying, not "the
feature doesn't work," so failures here report Health.failed but never
raise ActivationError / fall back to desk mode the way display.py does.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from ..config.schema import CursorConfig
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


def icons_default_theme_content(theme: str) -> str:
    return f"[Icon Theme]\nInherits={theme}\n"


async def _live_theme() -> str | None:
    """KWin's live cursor theme, or None if it can't be read."""
    rc, out = await _run(["qdbus6", "org.kde.KWin", "/KWin", "supportInformation"])
    if rc != 0:
        return None
    section = out.partition("\nCursor\n")[2]
    m = re.search(r"^themeName:\s*(\S+)\s*$", section, re.MULTILINE)
    return m.group(1) if m else None


async def _apply_theme(theme: str, config: CursorConfig, health: Health) -> None:
    icons_default = Path.home() / ".icons" / "default"
    try:
        icons_default.mkdir(parents=True, exist_ok=True)
        (icons_default / "index.theme").write_text(icons_default_theme_content(theme))
    except OSError as e:
        health.failed("cursor", f"failed to write ~/.icons/default/index.theme: {e}")
        return

    rc, out = await _run(["plasma-apply-cursortheme", theme])
    if rc != 0:
        health.failed("cursor", f"failed to apply cursor theme {theme}: {out.strip()}")
        return

    live = await _live_theme()
    if live is None:
        health.failed("cursor", f"applied {theme} but could not read KWin's live cursor theme back")
        return
    if live == theme:
        health.ok("cursor", f"cursor theme set to {theme}")
        return

    # Files and compositor have drifted apart; plasma-apply-cursortheme is a
    # no-op when kcminputrc already matches. Force a real change via another theme.
    nudge = next((t for t in (config.hide_theme, config.normal_theme) if t and t != theme), "default")
    logger.warning("cursor: KWin live theme is %r after applying %r, forcing via %r", live, theme, nudge)
    await _run(["plasma-apply-cursortheme", nudge])
    rc, out = await _run(["plasma-apply-cursortheme", theme])
    if rc != 0:
        health.failed("cursor", f"failed to apply cursor theme {theme} after forcing: {out.strip()}")
        return
    live = await _live_theme()
    if live == theme:
        health.ok("cursor", f"cursor theme set to {theme} (forced past stale KWin state)")
    else:
        health.failed("cursor", f"KWin live cursor theme is {live!r}, wanted {theme!r}")


async def activate_couch(config: CursorConfig, health: Health) -> None:
    if not config.enabled:
        return
    await _apply_theme(config.hide_theme, config, health)


async def activate_desk(config: CursorConfig, health: Health) -> None:
    if not config.enabled:
        return
    if not config.normal_theme:
        # Nothing configured to restore to -- leave whatever's currently
        # set alone rather than guessing at a theme name that might not
        # exist on this host.
        health.ok("cursor", "no normal_theme configured, leaving cursor theme as-is")
        return
    await _apply_theme(config.normal_theme, config, health)
