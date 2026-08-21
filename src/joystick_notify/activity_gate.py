"""The activity gate — sits between `Debouncer` and `StateMachine`, and
answers a question debounce.py deliberately doesn't: not "is this signal
stable" but "does this presence represent genuine intent to play."

Confirmed as a real, live bug 2026-08-21: a Steam Controller Puck receiver
left connected from a prior session was still producing idle HID traffic
when the daemon (re)started. Debounce correctly saw a *stable* connect —
that part worked exactly as designed — and forwarded it straight to the
state machine, which correctly activated couch mode for a controller
nobody was touching. v1 patched the specific symptom (skip the startup
scan if we were last in desk mode, via `LAST_MODE_FILE`) but never solved
the general case: the same false positive would happen mid-session too,
for a controller plugged in only to charge while the daemon keeps running.

The fix is categorical, not situational: a bare "connected" is not
trustworthy on its own. `ActivityGate` holds every first-time connect
until the device's *real* evdev stream (actual decoded button presses /
meaningful stick movement — not mere hidraw byte presence, which is
exactly what caused the false positive) proves someone is actually using
it, and only then forwards the same `DeviceEvent` shape downstream —
`StateMachine`'s contract is completely unchanged, it has no idea this
gate exists. A device that disconnects before ever proving activity
simply never triggers anything, by construction, not by remembering
history in a file.

Gating is deliberately asymmetric: strict on the way in (a bare connect
proves nothing), lenient on the way out (once a device *has* proven
activity and become the owner, mere continued presence is exactly what
should keep couch mode alive — a pause in gameplay must not look like
"not active" and tear things down). Disconnects always pass through
immediately, regardless of whether the device ever passed the gate.

There is deliberately no fallback timeout that trusts a connect anyway
after waiting long enough — that would silently reopen the exact false-
positive class this exists to close. If a device is genuinely never
touched, waiting forever for activity that will never come is the
*correct* outcome, not a hung state: nothing should ever happen for it.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Protocol

from .debounce import DeviceEvent, StableKind

logger = logging.getLogger(__name__)


class ActivityDetector(Protocol):
    async def wait_for_activity(self, device_id: str) -> None:
        """Blocks until real input activity is observed for device_id.
        The only early return is cancellation (the gate cancels this when
        the device disconnects before ever proving activity)."""
        ...


class ActivityGate:
    def __init__(
        self,
        emit: Callable[[DeviceEvent], Awaitable[None]],
        detector: ActivityDetector | None = None,
    ) -> None:
        self._emit = emit
        self._detector = detector or EvdevActivityDetector()
        self._pending: dict[str, asyncio.Task] = {}
        self._activated: set[str] = set()

    async def handle(self, event: DeviceEvent) -> None:
        if event.kind == StableKind.DISCONNECTED:
            self._cancel(event.device_id)
            self._activated.discard(event.device_id)
            await self._emit(event)
            return

        if event.device_id in self._activated:
            # Already proven active and still connected — presence alone
            # is sufficient from here on, no re-gating on every event.
            await self._emit(event)
            return

        logger.info("activity_gate[%s]: connected, waiting for real input before forwarding", event.device_id)
        self._cancel(event.device_id)
        self._pending[event.device_id] = asyncio.ensure_future(self._wait_and_forward(event))

    async def _wait_and_forward(self, event: DeviceEvent) -> None:
        try:
            await self._detector.wait_for_activity(event.device_id)
        except asyncio.CancelledError:
            logger.info("activity_gate[%s]: disconnected before proving activity, never forwarded", event.device_id)
            return
        self._pending.pop(event.device_id, None)
        self._activated.add(event.device_id)
        logger.info("activity_gate[%s]: real activity observed, forwarding connect", event.device_id)
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


class EvdevActivityDetector:
    """Real implementation: resolves device_id to its /dev/input/eventN
    node and watches for an actual EV_KEY press or an EV_ABS axis moving
    meaningfully away from its at-open rest position (a proportional
    deadzone computed from the axis's own reported min/max range, not a
    fixed magic number — different controllers report wildly different
    raw ranges).
    """

    ABS_DEADZONE_FRACTION = 0.15

    async def wait_for_activity(self, device_id: str) -> None:
        from .devices.detect import find_evdev_path_for_device

        path = find_evdev_path_for_device(device_id)
        if path is None:
            # No evdev node resolvable for this device at all -- "wait for
            # real activity" is undecidable without one, so fail open and
            # trust the debounced connect as-is rather than block forever
            # on a device we have no way to ever observe.
            logger.warning("activity_gate[%s]: no evdev node found, trusting connect as-is", device_id)
            return

        import evdev

        try:
            dev = evdev.InputDevice(path)
        except OSError as e:
            logger.warning("activity_gate[%s]: could not open %s (%s), trusting connect as-is", device_id, path, e)
            return

        rest: dict[int, int] = {}
        activity = asyncio.Event()
        loop = asyncio.get_event_loop()

        def deadzone_for(code: int) -> float:
            try:
                info = dev.absinfo(code)
            except (OSError, KeyError):
                return 0.0
            if info is None:
                return 0.0
            return abs(info.max - info.min) * self.ABS_DEADZONE_FRACTION

        def on_readable() -> None:
            try:
                events = list(dev.read())
            except (OSError, BlockingIOError):
                return
            for event in events:
                if event.type == evdev.ecodes.EV_KEY and event.value == 1:
                    activity.set()
                    return
                if event.type == evdev.ecodes.EV_ABS:
                    baseline = rest.setdefault(event.code, event.value)
                    if abs(event.value - baseline) > deadzone_for(event.code):
                        activity.set()
                        return

        loop.add_reader(dev.fd, on_readable)
        try:
            await activity.wait()
        finally:
            loop.remove_reader(dev.fd)
            dev.close()
