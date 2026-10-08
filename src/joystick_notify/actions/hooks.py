"""User-defined lifecycle hooks: N shell commands per phase, run around the
couch/desk transitions. The daemon knows nothing about what they do (hide the
mouse, pause a service, flip a light) -- that is deliberately the user's
business, since the right way varies per distro and desktop.

Phases (config keys under [hooks]):
  prestart   before couch mode is activated
  poststart  after couch mode is active and the launch command has fired
  preexit    before desk mode is activated (the launched process is still up)
  postexit   after desk mode is active

Guarantees:
  * Commands in a phase run strictly in order, one at a time.
  * A failing, hanging or missing command NEVER blocks or aborts the
    transition and never stops the rest of its phase: it is killed after
    `timeout_s` (whole process group) and logged.
  * Each command is a `/bin/sh -c` line with the session environment plus
    JOYSTICK_NOTIFY_PHASE / JOYSTICK_NOTIFY_MODE, so it works the same from the
    daemon, the wizard or a login shell.
  * Hooks should be idempotent: a phase can run twice (e.g. a desk switch
    forced while already in desk during startup reconciliation).
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal

logger = logging.getLogger(__name__)

PHASES = ("prestart", "poststart", "preexit", "postexit")
DEFAULT_TIMEOUT_S = 30.0
_KILL_GRACE_S = 2.0
_OUTPUT_LOG_LIMIT = 500


def clean_commands(raw) -> list[str]:
    """Normalize a hook list from config/form input: strings only, stripped,
    blanks dropped, order kept."""
    if not isinstance(raw, (list, tuple)):
        return []
    return [c.strip() for c in raw if isinstance(c, str) and c.strip()]


async def _kill_group(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), _KILL_GRACE_S)
    except asyncio.TimeoutError:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await proc.wait()


async def run_command(command: str, phase: str, mode: str, timeout_s: float) -> bool:
    """Run one hook command. True on exit 0; never raises."""
    env = dict(os.environ)
    env["JOYSTICK_NOTIFY_PHASE"] = phase
    env["JOYSTICK_NOTIFY_MODE"] = mode
    try:
        proc = await asyncio.create_subprocess_exec(
            "/bin/sh", "-c", command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
    except OSError as e:
        logger.error("hooks[%s]: could not start %r: %s", phase, command, e)
        return False
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout_s)
    except asyncio.TimeoutError:
        logger.error("hooks[%s]: %r timed out after %.0fs, killing it", phase, command, timeout_s)
        await _kill_group(proc)
        return False
    except asyncio.CancelledError:
        await _kill_group(proc)
        raise
    text = out.decode(errors="replace").strip()[:_OUTPUT_LOG_LIMIT]
    if proc.returncode != 0:
        logger.error("hooks[%s]: %r exited %s: %s", phase, command, proc.returncode, text)
        return False
    logger.info("hooks[%s]: %r ok%s", phase, command, f": {text}" if text else "")
    return True


async def run_phase(phase: str, commands: list[str], mode: str, timeout_s: float = DEFAULT_TIMEOUT_S) -> list[str]:
    """Run every command of `phase` in order. Returns the commands that
    failed (empty = all good). Never raises (except cancellation)."""
    failed: list[str] = []
    for command in clean_commands(commands):
        if not await run_command(command, phase, mode, timeout_s):
            failed.append(command)
    return failed
