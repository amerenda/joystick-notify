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
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from .debounce import DeviceEvent, StableKind

logger = logging.getLogger(__name__)

DEFAULT_STARTUP_GRACE_S = 10.0


class ActivityGate:
    def __init__(
        self,
        emit: Callable[[DeviceEvent], Awaitable[None]],
        *,
        startup_grace_s: float = DEFAULT_STARTUP_GRACE_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._emit = emit
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
        # genuinely fresh, could be the stale-carryover case. Hold it
        # until either the grace window passes or it disconnects.
        logger.info(
            "activity_gate[%s]: connected during startup grace window with no known prior state, holding briefly",
            event.device_id,
        )
        self._cancel(event.device_id)
        self._pending[event.device_id] = asyncio.ensure_future(self._wait_for_grace_then_forward(event))

    async def _wait_for_grace_then_forward(self, event: DeviceEvent) -> None:
        remaining = self._startup_grace_s - (self._clock() - self._started_at)
        try:
            if remaining > 0:
                await asyncio.sleep(remaining)
        except asyncio.CancelledError:
            logger.info("activity_gate[%s]: disconnected during startup grace window, never forwarded", event.device_id)
            return
        self._pending.pop(event.device_id, None)
        logger.info("activity_gate[%s]: startup grace window passed while still connected, forwarding", event.device_id)
        await self._emit(event)

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
