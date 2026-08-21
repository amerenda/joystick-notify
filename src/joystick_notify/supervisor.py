"""Wraps every background task this daemon spawns so an unexpected
exception is caught, reported to Health as failed, and logged loudly --
rather than asyncio's default "Task exception was never retrieved",
which silently prints to stderr and otherwise leaves no trace.

Confirmed as a real gap during the 2026-08 architecture audit: every
background task in this codebase (state_machine's owner_watch/
disconnect_grace, the wizard's embedded uvicorn server, the hidraw
liveness watcher, debounce/activity-gate per-device timers, the CEC
retry loop, the manual-exit shortcut watcher) was fire-and-forget with
no exception handling anywhere. Worst case found: the wizard's Health
entry is set once at startup ("listening on ...") and never touched
again -- if the embedded server crashed later, health.json would report
it as fine forever, and the tray would show green for a dead wizard.

`supervise()` is the one place this gets fixed, rather than requiring
every spawn site to remember to wrap itself.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable

from .health import Health

logger = logging.getLogger(__name__)


def supervise(name: str, coro: Awaitable[None], health: Health) -> asyncio.Task:
    """Wraps `coro` in a Task exactly like a bare `asyncio.ensure_future(coro)`
    would (same return type, same cancellation semantics -- callers that
    used to hold onto that Task directly, like state_machine's
    self._tasks[name] or cec_control's returned retry-loop Task, keep
    working unchanged), but attaches a done-callback that reports any
    uncaught exception via health.failed(name, ...) instead of letting it
    vanish into asyncio's default "Task exception was never retrieved".

    Deliberately NOT an extra `async def _runner(): await coro` wrapper
    layer -- wrapping the *wrapper* instead of `coro` directly means `coro`
    is never actually incorporated into a Task if the wrapper gets
    cancelled before its first scheduled step, which orphans `coro` as a
    bare, never-awaited coroutine object (confirmed via a real
    RuntimeWarning during testing). add_done_callback on the real task
    avoids that entirely.
    """
    task = asyncio.ensure_future(coro)

    def _on_done(t: asyncio.Task) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.error("supervisor[%s]: background task crashed", name, exc_info=exc)
            health.failed(name, "background task crashed", str(exc))

    task.add_done_callback(_on_done)
    return task
