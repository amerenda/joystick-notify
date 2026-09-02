"""Tears down a live couch session to desk BEFORE the system actually
powers off or reboots, not just after the fact.

This is the other half of the 2026-09-02 fresh-boot fix
(`boot_time.py`/`state_machine.reconcile_startup_mode`): that fix
corrects a bad display state discovered on the way *up*, after Alex shut
the PC down while still in couch mode and the desk monitor came back
with no signal at all. This module closes the same gap on the way
*down* -- run the real desk teardown while there's still time to, so the
bad state never gets left behind in the first place.

Holds a systemd-logind shutdown inhibitor lock (`mode=delay`) for the
daemon's entire lifetime and reacts to the `PrepareForShutdown` signal.
Acquiring the lock needs a real Unix file descriptor from `Inhibit()`,
which requires SCM_RIGHTS support -- `jeepney`'s asyncio backend doesn't
have it (built on `asyncio.open_unix_connection`, which exposes no
ancillary-data API), so that one call goes through `jeepney.io.blocking`
(raw socket, `enable_fds=True`) run in a thread instead. The long-lived
signal watch has no fd involved and uses the normal asyncio backend.

Bounded by `teardown_timeout_s`: whatever `force_exit_to_desk()` is
doing, this releases the inhibitor lock unconditionally once the timeout
elapses, so a bug or a slow CEC negotiation here can never hang the
actual shutdown -- worst case is identical to before this module
existed (no teardown), never worse. 13s default leaves a margin under
the 15s `InhibitDelayMaxSec` logind is configured for (see
`ansible-playbooks`' `roles/joystick-notify` logind.conf.d drop-in) --
enough for the fast synchronous steps (display/audio/cursor/screen-lock)
to reliably finish; CEC standby to the TV (best-effort everywhere else
in this codebase too, up to ~30s per target) gets a head start but may
not always complete.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import AsyncIterator, Awaitable, Callable, Optional

from .event_log import headline
from .health import Health

logger = logging.getLogger(__name__)

DEFAULT_TEARDOWN_TIMEOUT_S = 13.0

_LOGIND_BUS_NAME = "org.freedesktop.login1"
_LOGIND_OBJECT_PATH = "/org/freedesktop/login1"
_LOGIND_INTERFACE = "org.freedesktop.login1.Manager"


async def acquire_shutdown_inhibitor() -> Optional[int]:
    """Real implementation: calls logind's Inhibit() over a blocking,
    fd-enabled D-Bus connection (see module docstring for why), run in a
    thread so it doesn't block the event loop. Returns the raw fd to
    hold open, or None if logind is unreachable or refuses the lock --
    callers must treat None as "this protection isn't available," not
    retry in a tight loop.
    """

    def _call() -> Optional[int]:
        from jeepney import DBusAddress, MessageType, new_method_call
        from jeepney.io.blocking import open_dbus_connection

        login1 = DBusAddress(
            object_path=_LOGIND_OBJECT_PATH,
            bus_name=_LOGIND_BUS_NAME,
            interface=_LOGIND_INTERFACE,
        )
        try:
            conn = open_dbus_connection(bus="SYSTEM", enable_fds=True)
        except OSError:
            logger.warning("shutdown_watcher: couldn't connect to the system D-Bus")
            return None
        try:
            msg = new_method_call(
                login1,
                "Inhibit",
                "ssss",
                ("shutdown", "joystick-notify", "return to desk mode before power-off", "delay"),
            )
            reply = conn.send_and_get_reply(msg, timeout=5)
            if reply.header.message_type != MessageType.method_return:
                logger.warning("shutdown_watcher: logind refused the shutdown inhibitor lock: %r", reply.body)
                return None
            return reply.body[0].to_raw_fd()
        finally:
            conn.close()

    return await asyncio.to_thread(_call)


def prepare_for_shutdown_match_rule():
    """The match rule used both to subscribe (AddMatch, sent to the bus)
    and to locally route received messages to our queue (jeepney's own
    client-side re-check) -- see `watch_prepare_for_shutdown`'s docstring
    for why this must NOT include `sender=`, confirmed live 2026-09-02.
    Factored out so it can be asserted against directly in tests without
    a real bus connection.
    """
    from jeepney.bus_messages import MatchRule

    return MatchRule(
        type="signal",
        interface=_LOGIND_INTERFACE,
        member="PrepareForShutdown",
        path=_LOGIND_OBJECT_PATH,
    )


async def watch_prepare_for_shutdown() -> AsyncIterator[bool]:
    """Real implementation: yields True/False each time logind emits
    PrepareForShutdown on the system bus (True = shutdown/reboot
    starting, False = a prior one was cancelled). Runs for as long as
    it's iterated; the caller closing the generator (breaking out of a
    `async for`) tears down this D-Bus connection.

    Deliberately no `sender=` on this rule -- confirmed live 2026-09-02
    that adding it makes this silently never fire. The system bus daemon
    resolves a well-known name like `org.freedesktop.login1` to whichever
    unique connection currently owns it when matching an AddMatch rule,
    so the *subscription* itself works fine either way -- but `jeepney`'s
    own client-side re-filtering (`MatchRule.matches()`, used to route an
    already-received message to the right local queue) does a literal
    string comparison against the message's actual `sender` header
    field, which is always the unique name (e.g. `:1.4`), never the
    well-known one. With `sender` set, every real broadcast arrives and
    is then silently dropped by that local check -- `queue.get()` blocks
    forever, logind's own inhibitor-timeout log is the only trace
    ("Delay lock is active... but inhibitor timeout is reached"), and no
    `shutdown_watcher` log line appears at all. interface+member+path is
    already unambiguous (nothing else on the bus emits
    `org.freedesktop.login1.Manager.PrepareForShutdown` at this path), so
    dropping `sender` here costs nothing.
    """
    from jeepney.bus_messages import message_bus
    from jeepney.io.asyncio import open_dbus_router

    rule = prepare_for_shutdown_match_rule()
    async with open_dbus_router(bus="SYSTEM") as router:
        await router.send_and_get_reply(message_bus.AddMatch(rule))
        with router.filter(rule) as queue:
            while True:
                msg = await queue.get()
                yield bool(msg.body[0])


class ShutdownWatcher:
    def __init__(
        self,
        force_exit_to_desk: Callable[[], Awaitable[None]],
        is_couch: Callable[[], bool],
        health: Health,
        *,
        teardown_timeout_s: float = DEFAULT_TEARDOWN_TIMEOUT_S,
        acquire_inhibitor: Callable[[], Awaitable[Optional[int]]] = acquire_shutdown_inhibitor,
        release_inhibitor: Callable[[int], None] = os.close,
        shutdown_signals: Callable[[], AsyncIterator[bool]] = watch_prepare_for_shutdown,
    ) -> None:
        self._force_exit_to_desk = force_exit_to_desk
        self._is_couch = is_couch
        self._health = health
        self._teardown_timeout_s = teardown_timeout_s
        self._acquire_inhibitor = acquire_inhibitor
        self._release_inhibitor = release_inhibitor
        self._shutdown_signals = shutdown_signals

    async def run(self) -> None:
        """Long-running task -- hold this as a supervised background task
        for the daemon's whole lifetime, same as any other watcher
        (manual_exit, cec-retry). Returns in every terminal case (gives
        up if a lock can never be acquired; finishes once a real
        shutdown has been handled) rather than looping -- once a True
        signal has been acted on, the system genuinely is going down and
        this process is about to be killed, so there is no "next time"
        left to protect in this run. A *cancelled* shutdown (a False
        signal with no preceding True) doesn't end this method at all --
        the single inhibitor lock and D-Bus connection acquired below
        are held across any number of cancel/retry cycles, handled
        entirely inside the `async for` loop.
        """
        fd = await self._acquire_inhibitor()
        if fd is None:
            logger.warning(
                "shutdown_watcher: couldn't acquire a shutdown inhibitor lock -- "
                "couch mode will NOT be torn down before power-off this session"
            )
            self._health.degraded(
                "shutdown_watcher",
                "no shutdown inhibitor lock",
                "logind unreachable, or the lock was refused",
            )
            return
        self._health.ok("shutdown_watcher", "holding shutdown inhibitor lock")
        try:
            async for shutting_down in self._shutdown_signals():
                if not shutting_down:
                    # A prior shutdown was cancelled -- keep holding this
                    # same lock and waiting, nothing to react to.
                    continue
                if self._is_couch():
                    headline(
                        logger,
                        "shutdown_watcher: shutdown starting while in couch mode, "
                        "forcing desk teardown before it proceeds",
                    )
                    try:
                        await asyncio.wait_for(self._force_exit_to_desk(), timeout=self._teardown_timeout_s)
                    except asyncio.TimeoutError:
                        logger.warning(
                            "shutdown_watcher: desk teardown did not finish within %.0fs, "
                            "releasing the shutdown lock anyway",
                            self._teardown_timeout_s,
                        )
                break
        finally:
            self._release_inhibitor(fd)
