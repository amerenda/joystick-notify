"""Couch/desk state machine: explicit states and transitions owned by one
asyncio.Lock, not lock files (`owner.lock`, `LAST_MODE_FILE`, etc. in v1).

This directly targets two of the audit's root patterns:

- **Pattern A** (detached background jobs, no lifecycle tracking): every
  background task this module starts (disconnect grace, owner/process
  watch loop) is a named `asyncio.Task` in `self._tasks`, cancelled
  deterministically on the next transition or on `aclose()`. There is no
  path where a stale retry can fire after teardown the way v1's CEC
  re-assert loop could (v1 worked around this with a `LAST_MODE_FILE`
  read inside the retry loop; here the task is simply cancelled).
- **Pattern B** (instant state checks with no grace period for async
  operations): `_transition()` is serialized behind one lock so two
  mode-flips arriving close together (the exact scenario that crashed
  Steam in v1, see `STEAM_STARTUP_GRACE`) can't race, and the owner/process
  watch loop below explicitly ignores "not yet visible" during
  `launch_startup_grace_s` instead of treating it as "never started."

Action implementations (CEC/display/audio/launch) are injected via
`ActionHooks` so this module stays testable with fakes — see
`tests/test_state_machine.py`.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable, Optional

from .debounce import DeviceEvent, StableKind
from .health import Health

logger = logging.getLogger(__name__)


class Mode(str, Enum):
    DESK = "desk"
    COUCH = "couch"


class ActivationError(Exception):
    """Raised by an action hook when a mode activation fails in a way that
    should be visible (Health.failed) and cause a fallback to desk mode —
    e.g. v1's display-control.sh giving up after 15 DRM-connector-status
    retries and explicitly returning to desk mode rather than leaving the
    system in a half-switched state.
    """

    def __init__(self, component: str, reason: str, detail: str = ""):
        super().__init__(reason)
        self.component = component
        self.reason = reason
        self.detail = detail


@dataclass
class ActionHooks:
    activate_couch: Callable[[], Awaitable[None]]
    activate_desk: Callable[[], Awaitable[None]]
    launch: Optional[Callable[[], Awaitable[None]]] = None
    is_launch_process_alive: Optional[Callable[[], Awaitable[bool]]] = None
    is_owner_present: Optional[Callable[[str], Awaitable[bool]]] = None


DEFAULT_DISCONNECT_GRACE_S = 30
DEFAULT_LAUNCH_STARTUP_GRACE_S = 10
DEFAULT_NO_CONTROLLER_TIMEOUT_S = 120
DEFAULT_POLL_INTERVAL_S = 2


class StateMachine:
    def __init__(
        self,
        hooks: ActionHooks,
        health: Health,
        *,
        disconnect_grace_s: float = DEFAULT_DISCONNECT_GRACE_S,
        launch_startup_grace_s: float = DEFAULT_LAUNCH_STARTUP_GRACE_S,
        no_controller_timeout_s: float = DEFAULT_NO_CONTROLLER_TIMEOUT_S,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    ) -> None:
        self.mode = Mode.DESK
        self._owner: str | None = None
        self._lock = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task] = {}
        self._hooks = hooks
        self._health = health
        self._launch_ts: float | None = None
        self._no_controller_since: float | None = None
        self._disconnect_grace_s = disconnect_grace_s
        self._launch_startup_grace_s = launch_startup_grace_s
        self._no_controller_timeout_s = no_controller_timeout_s
        self._poll_interval_s = poll_interval_s

    @property
    def owner(self) -> str | None:
        return self._owner

    async def handle_device_event(self, event: DeviceEvent) -> None:
        if event.kind == StableKind.CONNECTED:
            await self._on_connect(event.device_id)
        else:
            await self._on_disconnect(event.device_id)

    async def _on_connect(self, device_id: str) -> None:
        self._cancel_task("disconnect_grace")
        if self._owner is None:
            self._owner = device_id
            logger.info("state_machine[%s]: is now the owning controller", device_id)
        if self._owner != device_id:
            # A second controller connecting doesn't change mode ownership —
            # matches v1's single-owner lock semantics.
            logger.debug("state_machine[%s]: connected but owner is %s, ignoring", device_id, self._owner)
            return
        if self.mode == Mode.DESK:
            await self._transition(Mode.COUCH, device_id=device_id)

    async def _on_disconnect(self, device_id: str) -> None:
        if device_id != self._owner:
            logger.debug("state_machine[%s]: disconnected but owner is %s, ignoring", device_id, self._owner)
            return
        self._spawn_task("disconnect_grace", self._disconnect_grace_then_teardown(device_id))

    async def _disconnect_grace_then_teardown(self, device_id: str) -> None:
        try:
            await asyncio.sleep(self._disconnect_grace_s)
        except asyncio.CancelledError:
            return
        logger.info("state_machine[%s]: owner absent for %ss, tearing down to desk", device_id, self._disconnect_grace_s)
        await self._transition(Mode.DESK, device_id=device_id)

    async def _transition(self, target: Mode, *, device_id: str | None = None) -> None:
        tag = device_id or self._owner or "-"
        async with self._lock:
            if self.mode == target:
                return
            self._cancel_task("owner_watch")
            if target == Mode.COUCH:
                try:
                    await self._hooks.activate_couch()
                except ActivationError as e:
                    self._health.failed(e.component, e.reason, e.detail)
                    logger.error("state_machine[%s]: couch activation failed (%s: %s), staying in desk", tag, e.component, e.reason)
                    return
                self.mode = Mode.COUCH
                self._launch_ts = time.monotonic()
                self._no_controller_since = None
                if self._hooks.launch is not None:
                    await self._hooks.launch()
                self._spawn_task("owner_watch", self._owner_watch_loop())
            else:
                try:
                    await self._hooks.activate_desk()
                except ActivationError as e:
                    self._health.failed(e.component, e.reason, e.detail)
                    logger.error("state_machine[%s]: desk activation failed (%s: %s)", tag, e.component, e.reason)
                self.mode = Mode.DESK
                self._owner = None
                self._launch_ts = None
                self._no_controller_since = None
            self._health.ok("state_machine", f"mode={self.mode.value}")
            logger.info("state_machine[%s]: transitioned to %s", tag, self.mode.value)

    async def _owner_watch_loop(self) -> None:
        """Generalized version of v1's watcher-process.sh: auto-exits couch
        mode when either the launched process has exited, or the owner
        controller has been absent for `no_controller_timeout_s` — checked
        directly here (not just via the debounced disconnect event) as
        defense in depth, matching v1's comment that a permanently-attached
        second USB device shouldn't prevent this timer from ever firing.
        `launch_startup_grace_s` is the direct port of `STEAM_STARTUP_GRACE`:
        a cold process start takes time to become visible, so "not visible
        yet" must not be read as "already exited."
        """
        owner = self._owner
        try:
            while True:
                await asyncio.sleep(self._poll_interval_s)
                if self.mode != Mode.COUCH:
                    return
                elapsed = time.monotonic() - (self._launch_ts or 0.0)
                if elapsed < self._launch_startup_grace_s:
                    continue

                if self._hooks.is_launch_process_alive is not None:
                    alive = await self._hooks.is_launch_process_alive()
                    if not alive:
                        logger.info("state_machine[%s]: launched process exited -> tearing down to desk", owner)
                        await self._transition(Mode.DESK, device_id=owner)
                        return

                if self._hooks.is_owner_present is not None and self._owner is not None:
                    present = await self._hooks.is_owner_present(self._owner)
                    now = time.monotonic()
                    if present:
                        self._no_controller_since = None
                    else:
                        if self._no_controller_since is None:
                            self._no_controller_since = now
                        elif now - self._no_controller_since >= self._no_controller_timeout_s:
                            logger.info(
                                "state_machine[%s]: owner absent %ss (process still alive) -> tearing down to desk",
                                owner, self._no_controller_timeout_s,
                            )
                            await self._transition(Mode.DESK, device_id=owner)
                            return
        except asyncio.CancelledError:
            return

    def _spawn_task(self, name: str, coro: Awaitable) -> None:
        self._cancel_task(name)
        self._tasks[name] = asyncio.ensure_future(coro)

    def _cancel_task(self, name: str) -> None:
        task = self._tasks.pop(name, None)
        if task is not None:
            task.cancel()

    async def aclose(self) -> None:
        """Structured teardown: cancel and await every in-flight task
        deterministically, rather than leaving detached jobs behind."""
        tasks = list(self._tasks.values())
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
