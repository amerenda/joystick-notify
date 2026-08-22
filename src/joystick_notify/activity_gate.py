"""The activity gate — sits between `Debouncer` and `StateMachine`, and
answers a question debounce.py deliberately doesn't: not "is this signal
stable" but "did this presence just get discovered, or did the daemon
actually witness it happen."

Confirmed as a real, live bug 2026-08-21: a Steam Controller Puck receiver
left connected from a prior session was still producing idle HID traffic
when the daemon (re)started. Debounce correctly saw a *stable* connect —
that part worked exactly as designed — and forwarded it straight to the
state machine, which correctly activated couch mode for a controller
nobody was touching at that moment. v1 patched the specific symptom (skip
the startup scan entirely if we were last in desk mode, via
`LAST_MODE_FILE`) but never solved the general case: the same false
positive would happen mid-session too, for a controller plugged in only
to charge while the daemon keeps running.

First attempt at a fix here required a genuine evdev button press/stick
movement before trusting *any* connect, for the device's entire lifetime
under this daemon. Live testing immediately showed that's stricter than
what's actually wanted: a deliberate power-on is itself the "I want to
play" signal — requiring an extra button press on top of it is friction
nobody asked for, and it's not what closes the real bug anyway.

The actual fix only needs to distinguish two things:
- A device already present in the first few seconds after the daemon
  starts is ambiguous — it might be genuinely fresh, or it might be
  exactly the stale-carryover case that caused the bug. Worth a beat of
  caution.
- A device that transitions to connected well after startup, or that the
  daemon has directly *witnessed* disconnect at any point, is
  unambiguous: the daemon saw the real state change happen, so there's
  nothing left to be suspicious of. Trust it immediately, same as v1
  always did for any event arriving after its (unconditional) startup
  scan.

A second real incident, 2026-08-22, showed the "wait out the grace window,
then trust it anyway" fallback below was itself a bug, not just a
best-effort compromise: restarting the daemon (e.g. to deploy a fix) while
a controller genuinely happened to still be connected from an earlier,
unrelated session trusted it once the window passed and fired couch mode
for nobody touching it — same root shape as the original bug, just via
the timeout path instead of the startup-scan path. There's no way to
distinguish "already connected when we started watching" from "connected
in the first few seconds by coincidence" other than time, so a device
whose first-ever signal arrives inside the ambiguous window is now held
*indefinitely* — not just until the window passes. Only a genuinely
witnessed disconnect (a real power-cycle) re-arms trust for that device's
next connect. This does mean an unexpected daemon restart mid-session
won't auto-resume couch mode on its own; see state_machine.py's
stale-session recovery watch (keyed off the launched game's own lifecycle,
not the controller) for how that gap gets closed instead.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from .debounce import DeviceEvent, StableKind
from .health import Health
from .supervisor import supervise

logger = logging.getLogger(__name__)

DEFAULT_STARTUP_GRACE_S = 10.0


class ActivityGate:
    def __init__(
        self,
        emit: Callable[[DeviceEvent], Awaitable[None]],
        health: Health | None = None,
        *,
        startup_grace_s: float = DEFAULT_STARTUP_GRACE_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._emit = emit
        self._health = health
        self._startup_grace_s = startup_grace_s
        self._clock = clock
        self._started_at = clock()
        self._pending: dict[str, asyncio.Task] = {}
        # Devices the daemon has directly witnessed disconnect at least
        # once -- any later connect for these is unambiguous, not
        # carryover state from before the daemon existed.
        self._witnessed_disconnect: set[str] = set()

    def _past_startup_grace(self) -> bool:
        return (self._clock() - self._started_at) >= self._startup_grace_s

    async def handle(self, event: DeviceEvent) -> None:
        if event.kind == StableKind.DISCONNECTED:
            self._cancel(event.device_id)
            self._witnessed_disconnect.add(event.device_id)
            await self._emit(event)
            return

        if self._past_startup_grace() or event.device_id in self._witnessed_disconnect:
            # Either well past the ambiguous startup window, or a device
            # the daemon directly watched go absent at some point -- both
            # mean this connect is an unambiguous, freshly-witnessed
            # transition. Trust it immediately.
            await self._emit(event)
            return

        # Still within the startup grace window and this device_id has
        # never been seen disconnect under this daemon run -- could be
        # genuinely fresh, could be the stale-carryover case. Hold it; see
        # _wait_for_grace_then_give_up for what happens once the window
        # passes (not a "trust it anyway" fallback -- see module docstring).
        logger.info(
            "activity_gate[%s]: connected during startup grace window with no known prior state, holding",
            event.device_id,
        )
        self._cancel(event.device_id)
        coro = self._wait_for_grace_then_give_up(event)
        if self._health is not None:
            self._pending[event.device_id] = supervise(f"activity_gate:{event.device_id}", coro, self._health)
        else:
            self._pending[event.device_id] = asyncio.ensure_future(coro)

    async def _wait_for_grace_then_give_up(self, event: DeviceEvent) -> None:
        """Once the ambiguous startup window passes, a still-connected,
        never-witnessed-disconnected device stays untrusted indefinitely --
        this task's only job is to stop holding a reference once that
        point is reached (there's nothing left to wait for). Trust is
        re-armed only by handle() itself, the next time this device_id
        actually disconnects.
        """
        remaining = self._startup_grace_s - (self._clock() - self._started_at)
        try:
            if remaining > 0:
                await asyncio.sleep(remaining)
        except asyncio.CancelledError:
            logger.info("activity_gate[%s]: disconnected during startup grace window, never forwarded", event.device_id)
            return
        self._pending.pop(event.device_id, None)
        logger.info(
            "activity_gate[%s]: still connected after startup grace window with no witnessed disconnect -- "
            "treating as a stale carryover, not forwarding. Power-cycle the controller to start couch mode.",
            event.device_id,
        )

    def _cancel(self, device_id: str) -> None:
        task = self._pending.pop(device_id, None)
        if task is not None:
            task.cancel()

    async def aclose(self) -> None:
        tasks = list(self._pending.values())
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._pending.clear()
