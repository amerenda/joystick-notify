"""Launcher detection + launch presets. Generalizes v1's
scripts/launch-bigpicture.sh (which only ever did Steam Big Picture) into a
preset registry, per plans/joystick-notify-v2.md's action configuration
model: the wizard offers whichever launchers it actually finds installed,
defaulting to the proven Steam Big Picture path.

Out of scope for this pass, called out explicitly rather than silently
dropped: v1's `scripts/game-wrapper.sh` (the gamescope + KWin cursor-hide
wrapper set as `STEAM_COMPAT_COMMAND_PREFIX`, invoked by Steam per-game,
not by the daemon directly) has real per-game display/cursor logic that
belongs in its own installed helper script, not this module — porting it
faithfully is follow-up work, not part of the core daemon prototype.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


def detect_launchers(home: Path | None = None) -> dict[str, bool]:
    home = home or Path.home()
    candidates = {
        "steam": [home / ".steam", home / ".local/share/Steam"],
        "lutris": [home / ".local/share/lutris", home / ".var/app/net.lutris.Lutris"],
        "heroic": [home / ".config/heroic", home / ".var/app/com.heroicgameslauncher.hgl"],
        "bottles": [home / ".local/share/bottles", home / ".var/app/com.usebottles.bottles"],
        "itch": [home / ".config/itch"],
    }
    return {name: any(p.exists() for p in paths) for name, paths in candidates.items()}


def _is_steam_running(proc_root: str = "/proc") -> bool:
    return is_process_running(["steam"], proc_root=proc_root)


def is_process_running(name_patterns: list[str], proc_root: str = "/proc") -> bool:
    """Scans /proc for a process whose comm or cmdline matches any of
    `name_patterns`. `proc_root` is injectable for unit testing against a
    fake directory tree rather than the real /proc.
    """
    try:
        pids = [p for p in os.listdir(proc_root) if p.isdigit()]
    except OSError:
        return False
    for pid in pids:
        comm_path = os.path.join(proc_root, pid, "comm")
        try:
            with open(comm_path) as f:
                comm = f.read().strip()
        except OSError:
            comm = ""
        if any(pattern in comm for pattern in name_patterns):
            return True
        cmdline_path = os.path.join(proc_root, pid, "cmdline")
        try:
            with open(cmdline_path, "rb") as f:
                cmdline = f.read().decode(errors="replace")
        except OSError:
            cmdline = ""
        if any(pattern in cmdline for pattern in name_patterns):
            return True
    return False


async def _run_detached(cmd: list[str]) -> None:
    """Fire-and-forget from the caller's perspective (doesn't block the
    launch call), but NOT invisible in logs: a background task awaits the
    process and logs a nonzero exit with whatever it printed. Confirmed
    necessary via live testing 2026-08-21 -- discarding stdout/stderr to
    DEVNULL meant Steam's own "unable to open a connection to X" failure
    (a real launch failure, see session_env.py) was completely invisible
    in the daemon's logs; the only way we found out was watching the
    screen directly, which defeats the entire point of structured
    logging for something meant to run unattended.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
    except FileNotFoundError:
        logger.error("launchers: command not found: %s", cmd[0])
        return
    asyncio.ensure_future(_log_outcome(cmd, proc))


async def _log_outcome(cmd: list[str], proc: asyncio.subprocess.Process) -> None:
    out, _ = await proc.communicate()
    if proc.returncode != 0:
        logger.error(
            "launchers: %s exited %s: %s",
            cmd[0], proc.returncode, out.decode(errors="replace").strip()[:500],
        )
    else:
        logger.debug("launchers: %s exited 0", cmd[0])


async def launch_steam_bigpicture() -> None:
    """Direct port of launch-bigpicture.sh's core logic: reuse an existing
    Steam client if running (steam:// deep link avoids a second instance),
    otherwise cold-start straight into Big Picture / gamepadui mode.
    """
    if _is_steam_running():
        await _run_detached(["steam", "-ifrunning", "steam://open/bigpicture"])
    else:
        await _run_detached(["steam", "-gamepadui"])


LAUNCH_PRESETS: dict[str, Callable[[], Awaitable[None]]] = {
    "steam-bigpicture": launch_steam_bigpicture,
}


async def run_custom_command(command: str) -> None:
    await _run_detached(["/bin/sh", "-c", command])


async def launch(preset_or_command: str) -> None:
    preset = LAUNCH_PRESETS.get(preset_or_command)
    if preset is not None:
        await preset()
        return
    if preset_or_command:
        await run_custom_command(preset_or_command)


async def is_launch_process_alive(preset_or_command: str) -> bool:
    """Used by state_machine's owner-watch loop. Only Steam's process name
    is known well enough to check reliably; an unrecognized preset/custom
    command returns True (can't determine — matches the state machine's
    'grace period, don't guess' philosophy rather than false-triggering a
    teardown for something we simply don't know how to check).
    """
    if preset_or_command == "steam-bigpicture":
        return _is_steam_running()
    return True
