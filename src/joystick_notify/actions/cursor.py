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
and exits (confirmed live 2026-08-29, disassembly shows the exact string
"X11 backend not detected. Exit."). This session is KWin/Wayland, so its
effect is replicated here by hand, the same way ansible-playbooks'
roles/mouse-hide does it live from the control node:
  1. kwriteconfig6 sets kcminputrc's [Mouse] cursorTheme -- this is what
     KWin itself reads for its own compositor-drawn cursor, which is what
     Steam/Big Picture and everything else without its own custom cursor
     actually shows.
  2. qdbus6 org.kde.KWin /KWin reconfigure applies it live, no logout.
  3. ~/.icons/default/index.theme covers GTK/SDL apps that resolve "the
     cursor theme" via the classic Xcursor "default" convention instead
     of reading kcminputrc directly (relevant since Steam's own UI isn't
     a native Qt/KDE app).

Best-effort, like audio.py -- a stuck cursor theme is annoying, not "the
feature doesn't work," so failures here report Health.failed but never
raise ActivationError / fall back to desk mode the way display.py does.
"""
from __future__ import annotations

import asyncio
import logging
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


async def _apply_theme(theme: str, health: Health) -> None:
    rc, out = await _run(["kwriteconfig6", "--file", "kcminputrc", "--group", "Mouse", "--key", "cursorTheme", theme])
    if rc != 0:
        health.failed("cursor", f"failed to write cursorTheme={theme}: {out}")
        return

    icons_default = Path.home() / ".icons" / "default"
    try:
        icons_default.mkdir(parents=True, exist_ok=True)
        (icons_default / "index.theme").write_text(icons_default_theme_content(theme))
    except OSError as e:
        health.failed("cursor", f"failed to write ~/.icons/default/index.theme: {e}")
        return

    rc, out = await _run(["qdbus6", "org.kde.KWin", "/KWin", "reconfigure"])
    if rc != 0:
        health.failed("cursor", f"KWin reconfigure failed applying {theme}: {out}")
        return

    health.ok("cursor", f"cursor theme set to {theme}")


async def activate_couch(config: CursorConfig, health: Health) -> None:
    if not config.enabled:
        return
    await _apply_theme(config.hide_theme, health)


async def activate_desk(config: CursorConfig, health: Health) -> None:
    if not config.enabled:
        return
    if not config.normal_theme:
        # Nothing configured to restore to -- leave whatever's currently
        # set alone rather than guessing at a theme name that might not
        # exist on this host.
        health.ok("cursor", "no normal_theme configured, leaving cursor theme as-is")
        return
    await _apply_theme(config.normal_theme, health)
