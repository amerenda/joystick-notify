"""The debounce chokepoint — the architectural center of v2, not a footnote.

Every controller add/remove, regardless of source (pyudev uevent, a
synthetic re-scan on daemon start, or the hidraw-polling liveness fallback
ported from v1's controller-liveness-watch.py for receivers that emit zero
real uevents), normalizes into a `RawEvent` before it reaches this module.
`Debouncer` holds one `asyncio.Task` timer per device and only emits a
stable `DeviceEvent` after that device's state has held steady for its
configured window. Nothing downstream (state machine, CEC, display, audio)
ever sees a raw bounce — this is the structural fix for v1's Pattern C
(controller flapping is normal hardware behavior, not an edge case to
special-case per call site).

Cancellation is real: `asyncio.Task.cancel()`, not a convention to
remember (v1's Pattern A — detached `&` jobs with no lifecycle tracking).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

DEFAULT_DEBOUNCE_MS = 300


class RawKind(str, Enum):
    ADD = "add"
    REMOVE = "remove"


class StableKind(str, Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"


_RAW_TO_STABLE = {RawKind.ADD: StableKind.CONNECTED, RawKind.REMOVE: StableKind.DISCONNECTED}


@dataclass(frozen=True)
class RawEvent:
    device_id: str
    kind: RawKind
    device_class: str = "generic"
    source: str = "unknown"


@dataclass(frozen=True)
class DeviceEvent:
    device_id: str
    kind: StableKind
    device_class: str = "generic"


class Debouncer:
    """Per-device-class timing is configurable (the Steam Puck receiver and
    the 8BitDo dongle don't necessarily bounce identically per the v1
    audit), but the *code path* is one place, not one grace-period-shaped
    patch per consumer.
    """

    def __init__(
        self,
        emit: Callable[[DeviceEvent], Awaitable[None]],
        *,
        default_debounce_ms: int = DEFAULT_DEBOUNCE_MS,
        per_class_debounce_ms: dict[str, int] | None = None,
    ) -> None:
        self._emit = emit
        self._default_debounce_ms = default_debounce_ms
        self._per_class_debounce_ms = per_class_debounce_ms or {}
        self._last_stable: dict[str, StableKind] = {}
        self._pending_tasks: dict[str, asyncio.Task] = {}

    def _window_for(self, device_class: str) -> float:
        return self._per_class_debounce_ms.get(device_class, self._default_debounce_ms) / 1000.0

    def feed(self, event: RawEvent) -> None:
        """Non-blocking: schedules or cancels the per-device timer. Safe to
        call from any event source (udev callback, evdev reader, hidraw
        liveness poller) without awaiting anything.
        """
        # Every raw event that reaches the debouncer is logged here, in one
        # place, regardless of which source produced it — this is the
        # single line to `journalctl | grep <device_id>` for to see
        # everything a device did, no matter which detector saw it first.
        logger.debug(
            "debounce[%s]: raw %s (class=%s, source=%s)",
            event.device_id, event.kind.value, event.device_class, event.source,
        )
        target = _RAW_TO_STABLE[event.kind]
        current_pending = self._pending_tasks.get(event.device_id)
        if current_pending is not None:
            current_pending.cancel()
            del self._pending_tasks[event.device_id]

        if self._last_stable.get(event.device_id) == target:
            # Bounce absorbed: device is already in (or settling toward) this
            # state, so there's nothing new to debounce toward.
            logger.debug("debounce[%s]: bounce back to %s absorbed, no-op", event.device_id, target.value)
            return

        task = asyncio.ensure_future(self._settle(event.device_id, target, event.device_class))
        self._pending_tasks[event.device_id] = task

    async def _settle(self, device_id: str, target: StableKind, device_class: str) -> None:
        try:
            await asyncio.sleep(self._window_for(device_class))
        except asyncio.CancelledError:
            logger.debug("debounce[%s]: pending %s cancelled before settling", device_id, target.value)
            return
        self._last_stable[device_id] = target
        self._pending_tasks.pop(device_id, None)
        logger.info("debounce[%s]: settled -> %s", device_id, target.value)
        await self._emit(DeviceEvent(device_id=device_id, kind=target, device_class=device_class))

    async def aclose(self) -> None:
        """Structured teardown: await-and-cancel every in-flight timer
        deterministically, rather than leaving orphaned tasks behind on
        daemon shutdown.
        """
        tasks = list(self._pending_tasks.values())
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._pending_tasks.clear()
