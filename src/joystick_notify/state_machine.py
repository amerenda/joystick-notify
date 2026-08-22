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
from .event_log import headline
from .supervisor import supervise
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
    # Takes the owning device_id -- resolved by _transition() from
    # self._owner (always set by _on_connect before a COUCH transition can
    # happen) -- so hooks that need to know *who* just connected (e.g. the
    # manual-exit shortcut watcher, which opens that specific controller's
    # evdev node) don't need their own back-channel into the state machine.
    activate_couch: Callable[[str], Awaitable[None]]
    activate_desk: Callable[[], Awaitable[None]]
    launch: Optional[Callable[[], Awaitable[None]]] = None
    is_launch_process_alive: Optional[Callable[[], Awaitable[bool]]] = None
    is_owner_present: Optional[Callable[[str], Awaitable[bool]]] = None
    # Fired when the owner reconnects while ALREADY in COUCH mode (not a
    # fresh desk->couch transition, so activate_couch() doesn't run again)
    # -- for state that's tied to the specific evdev node a device landed
    # on, which can renumber across a brief mid-session disconnect/
    # reconnect. Confirmed gap 2026-08: the manual-exit shortcut watcher
    # only ever started once, at couch entry; a reconnect on a renumbered
    # /dev/input/eventN left it permanently dead for the rest of that
    # session even though nothing else about couch mode was affected.
    on_reconnect_while_couch: Optional[Callable[[str], Awaitable[None]]] = None
    # Whether anything is configured to launch at all -- distinct from
    # is_launch_process_alive(), which conservatively returns True when
    # nothing is configured (so the *process-exit* teardown check never
    # false-triggers for a pure display/audio-switching setup with no
    # launcher). The disconnect path needs the opposite default: "nothing
    # configured" must mean "there's no game to wait for," not "assume
    # something's running."
    has_launch_target: Optional[Callable[[], Awaitable[bool]]] = None
    # Couch-idle: controller absent, launched game still running. Screen-
    # saver + CEC standby fire here WITHOUT leaving Mode.COUCH -- display/
    # audio stay exactly as they are so a reconnect resumes instantly
    # instead of redoing the whole desk->couch activation.
    enter_couch_idle: Optional[Callable[[], Awaitable[None]]] = None
    exit_couch_idle: Optional[Callable[[], Awaitable[None]]] = None


DEFAULT_DISCONNECT_GRACE_S = 30
DEFAULT_LAUNCH_STARTUP_GRACE_S = 10
DEFAULT_IDLE_AFTER_S = 120
DEFAULT_POLL_INTERVAL_S = 2


class StateMachine:
    def __init__(
        self,
        hooks: ActionHooks,
        health: Health,
        *,
        disconnect_grace_s: float = DEFAULT_DISCONNECT_GRACE_S,
        launch_startup_grace_s: float = DEFAULT_LAUNCH_STARTUP_GRACE_S,
        idle_after_s: float = DEFAULT_IDLE_AFTER_S,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        wait_for_game_on_disconnect: bool = True,
        screensaver_enabled: bool = True,
    ) -> None:
        self.mode = Mode.DESK
        self._owner: str | None = None
        # Whether `self._owner` is a real, presence-trackable device --
        # False for a synthetic owner assigned by a non-controller trigger
        # (currently just force_enter_couch()'s "manual", but this is
        # deliberately a general concept, not a magic-string check, so any
        # future non-controller trigger -- e.g. a scheduled/rule-engine
        # entry -- gets the same correct behavior for free: see
        # _owner_watch_loop()'s docstring for why a synthetic owner must
        # never drive the absence/idle-timeout logic, and _on_connect()'s
        # promotion logic for what happens when a real controller connects
        # into a synthetically-owned session.
        self._owner_is_real_device = False
        self._lock = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task] = {}
        self._hooks = hooks
        self._health = health
        self._wait_for_game_on_disconnect = wait_for_game_on_disconnect
        self._screensaver_enabled = screensaver_enabled
        self._launch_ts: float | None = None
        self._no_controller_since: float | None = None
        self._idle = False
        self._disconnect_grace_s = disconnect_grace_s
        self._launch_startup_grace_s = launch_startup_grace_s
        self._idle_after_s = idle_after_s
        self._poll_interval_s = poll_interval_s

    @property
    def owner(self) -> str | None:
        return self._owner

    async def check_for_stale_session_at_startup(self) -> None:
        """Call once, right after construction, before the daemon starts
        processing any device events. If the configured game is ALREADY
        running at this exact moment, that's strong, direct evidence
        we're recovering from a restart mid-session (nothing else would
        have launched it) — arms a background watch that resyncs the
        hardware to desk once that game exits, since self.mode already
        says "desk" but the display/audio/CEC may still be set for couch
        from before the restart.

        Deliberately keyed off the game's own lifecycle, not the
        controller's connection state: activity_gate.py's 2026-08-22 fix
        means a controller that's still connected at startup is (now,
        correctly) never auto-trusted, so it can't be used to infer
        anything here — but a process that's demonstrably alive right now
        couldn't have started itself.
        """
        if self._hooks.has_launch_target is None or self._hooks.is_launch_process_alive is None:
            return
        if not await self._hooks.has_launch_target():
            return
        if not await self._hooks.is_launch_process_alive():
            return
        logger.info(
            "state_machine: configured game already running at daemon startup -- "
            "likely resuming after a restart mid-session, arming a desk-resync watch"
        )
        self._spawn_task("stale_session_recovery", self._stale_session_recovery_watch())

    async def _stale_session_recovery_watch(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._poll_interval_s)
                if self.mode != Mode.DESK:
                    # A real, witnessed connect already brought the
                    # daemon's tracking back in sync (see _on_connect) --
                    # this watch's job is done.
                    return
                if self._hooks.is_launch_process_alive is None:
                    return
                if not await self._hooks.is_launch_process_alive():
                    headline(
                        logger,
                        "state_machine: launched game exited after a mid-session daemon restart -- "
                        "forcing a desk resync (display/audio/CEC may still have been set for couch)",
                    )
                    await self._teardown_from("stale_session_recovery", None, force=True)
                    return
        except asyncio.CancelledError:
            return

    async def force_exit_to_desk(self) -> None:
        """Manual override for the controller-shortcut exit path: tears down
        to desk unconditionally, regardless of owner/disconnect/process
        state. Reuses the exact same _transition(Mode.DESK) machinery as
        every other teardown trigger (disconnect grace, process-exit,
        no-controller-timeout) rather than a separate code path, so it gets
        the same self-deregistration protection against being cancelled
        mid-flight, and the same guarantee that only CEC/display/audio/
        screen-lock ever run — activate_desk() never touches the launched
        process, so this can never stop Steam or the game.
        """
        if self.mode == Mode.DESK:
            return
        await self._transition(Mode.DESK, device_id=self._owner)

    async def force_enter_couch(self) -> None:
        """Manual override for the wizard/API "switch to couch" action: the
        mirror of force_exit_to_desk(), activating couch mode unconditionally
        with no controller event involved. Assigns the synthetic owner id
        "manual" so activate_couch()/the owner-watch loop have something to
        key off of, and marks it explicitly non-real (`_owner_is_real_device
        = False`) -- this is what tells _owner_watch_loop() to skip the
        absence/idle-timeout logic entirely (a synthetic id is never
        "present," so without this a manually-triggered session would
        silently go couch-idle, powering everything back off, the moment
        idle_after_s elapsed -- confirmed live 2026-08-22). The manual-exit
        shortcut watcher also finds no evdev node for "manual" and skips
        itself for now (see ManualExitWatcher.start()) -- but if a real
        controller connects later in this same session, _on_connect()
        promotes it to real ownership, which turns both of these back on.

        self.mode == Mode.DESK here always means self._owner is None (see
        _transition()'s DESK branch), so there's no existing owner to
        preserve.
        """
        if self.mode == Mode.COUCH:
            return
        self._owner = "manual"
        self._owner_is_real_device = False
        await self._transition(Mode.COUCH, device_id=self._owner)

    async def handle_device_event(self, event: DeviceEvent) -> None:
        if event.kind == StableKind.CONNECTED:
            await self._on_connect(event.device_id)
        else:
            await self._on_disconnect(event.device_id)

    async def _on_connect(self, device_id: str) -> None:
        self._cancel_task("disconnect_grace")
        if self._owner is None:
            self._owner = device_id
            self._owner_is_real_device = True
            headline(logger, "state_machine[%s]: is now the owning controller", device_id)
        elif not self._owner_is_real_device and self._owner != device_id:
            # A real controller connecting into a synthetically-owned
            # session (started via force_enter_couch(), e.g. the wizard's
            # "Switch to Couch Mode" button) adopts real ownership from
            # here on -- this is what turns on presence-based idle
            # tracking and the manual-exit shortcut watcher for a session
            # that started with neither. Never displaces an EXISTING real
            # owner (guarded by `not self._owner_is_real_device`); a
            # second real controller connecting still just gets ignored
            # below, same single-owner lock semantics as always.
            headline(
                logger,
                "state_machine[%s]: adopting real ownership of a synthetically-started session (was %s)",
                device_id, self._owner,
            )
            self._owner = device_id
            self._owner_is_real_device = True
        if self._owner != device_id:
            # A second controller connecting doesn't change mode ownership —
            # matches v1's single-owner lock semantics.
            logger.debug("state_machine[%s]: connected but owner is %s, ignoring", device_id, self._owner)
            return
        if self.mode == Mode.DESK:
            await self._transition(Mode.COUCH, device_id=device_id)
        else:
            # Already in COUCH -- this is a reconnect (e.g. a brief
            # Bluetooth drop), not a fresh activation, so activate_couch()
            # must not run again. Hooks that opened something tied to the
            # old connection's specific device node still need a chance to
            # reopen it against whatever node the reconnect landed on.
            headline(logger, "state_machine[%s]: reconnected while already in couch mode", device_id)
            if self._idle:
                headline(logger, "state_machine[%s]: waking from couch idle (screensaver + TV standby)", device_id)
                if self._hooks.exit_couch_idle is not None:
                    await self._hooks.exit_couch_idle()
                self._idle = False
            if self._hooks.on_reconnect_while_couch is not None:
                await self._hooks.on_reconnect_while_couch(device_id)

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
        if self._wait_for_game_on_disconnect and await self._game_still_running():
            logger.info(
                "state_machine[%s]: owner absent for %ss but the launched game is still running, staying in couch mode",
                device_id, self._disconnect_grace_s,
            )
            return
        headline(logger, "state_machine[%s]: owner absent for %ss, tearing down to desk", device_id, self._disconnect_grace_s)
        await self._teardown_from("disconnect_grace", device_id)

    async def _game_still_running(self) -> bool:
        """True only when something is actually configured to launch AND
        it's currently alive -- distinct from is_launch_process_alive()'s
        own conservative "nothing configured -> True" default (see
        ActionHooks.has_launch_target's docstring for why that default is
        wrong for this specific decision: no game configured means there's
        nothing to wait for, so a disconnect should tear down immediately,
        same as it always has for a pure display/audio-switching setup).
        """
        if self._hooks.has_launch_target is None or self._hooks.is_launch_process_alive is None:
            return False
        if not await self._hooks.has_launch_target():
            return False
        return await self._hooks.is_launch_process_alive()

    async def _teardown_from(self, task_name: str, device_id: str | None, *, force: bool = False) -> None:
        """Every teardown trigger that runs *as* a named, cancellable task
        in self._tasks (disconnect_grace, owner_watch, stale_session_recovery)
        must call this, never `_transition` directly — deregistering
        `task_name` first is what stops `_transition`'s unconditional
        `_cancel_task("owner_watch")` from being a self-cancellation when
        the caller IS that task, and stops an unrelated event (a reconnect
        calling `_cancel_task("disconnect_grace")`) from cancelling this
        same task mid-flight once it's already committed to tearing down.
        Both are real incidents from 2026-08-21 live testing, not
        hypothetical: a self-cancellation silently aborted activate_desk()
        partway through (logged "tearing down to desk", then nothing else
        for over a minute), and a reconnect racing an in-flight
        disconnect-grace teardown cut its CEC standby retry loop off
        mid-sequence.

        `force=True` is for _stale_session_recovery_watch: self.mode
        already says "desk" (the daemon never actually entered couch mode
        this lifetime), so the normal same-mode no-op guard in
        _transition() would otherwise skip running activate_desk() at all
        — force bypasses that guard to actually resync the hardware.

        This MUST happen here, synchronously, with no `await` in between —
        moving it inside `_transition()` itself (e.g. under the lock) was
        tried and rejected: if the lock is contended, the caller can
        suspend *before* reaching the deregistration, leaving a window
        where the same race reopens.
        """
        self._tasks.pop(task_name, None)
        await self._transition(Mode.DESK, device_id=device_id, force=force)

    async def _transition(self, target: Mode, *, device_id: str | None = None, force: bool = False) -> None:
        tag = device_id or self._owner or "-"
        async with self._lock:
            if self.mode == target and not force:
                return
            self._cancel_task("owner_watch")
            self._cancel_task("stale_session_recovery")
            if target == Mode.COUCH:
                try:
                    await self._hooks.activate_couch(self._owner)
                except ActivationError as e:
                    self._health.failed(e.component, e.reason, e.detail)
                    logger.error("state_machine[%s]: couch activation failed (%s: %s), staying in desk", tag, e.component, e.reason)
                    return
                self.mode = Mode.COUCH
                self._launch_ts = time.monotonic()
                self._no_controller_since = None
                self._idle = False
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
                self._owner_is_real_device = False
                self._launch_ts = None
                self._no_controller_since = None
                self._idle = False
            self._health.ok("state_machine", f"mode={self.mode.value}")
            headline(logger, "state_machine[%s]: transitioned to %s", tag, self.mode.value)

    async def _owner_watch_loop(self) -> None:
        """Generalized version of v1's watcher-process.sh: auto-exits couch
        mode to desk when the launched game has exited -- checked directly
        here (not just via the debounced disconnect event) as defense in
        depth, matching v1's comment that a permanently-attached second USB
        device shouldn't prevent this timer from ever firing.
        `launch_startup_grace_s` is the direct port of `STEAM_STARTUP_GRACE`:
        a cold process start takes time to become visible, so "not visible
        yet" must not be read as "already exited."

        Separately, if the owner controller has been absent for
        `no_controller_timeout_s` *while the game is still running*, this
        goes couch-idle (screensaver + TV standby) rather than tearing down
        to desk -- see the note on that branch below for why staying in
        Mode.COUCH matters (an instant resume on reconnect, not a redo of
        the whole desk->couch activation).

        That whole absence/idle branch is gated on `_owner_is_real_device`
        -- a synthetic owner (force_enter_couch()'s "manual") is never
        "present" to begin with, so without this gate a manually-triggered
        session would silently go couch-idle (powering the TV back off)
        the moment idle_after_s elapsed, even with someone sitting right
        there -- confirmed live 2026-08-22. The process-exit teardown
        check above this is NOT gated -- if the launched game exits, that
        should still tear down to desk regardless of how the session
        started.
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
                        headline(logger, "state_machine[%s]: launched process exited -> tearing down to desk", owner)
                        await self._teardown_from("owner_watch", owner)
                        return

                if (
                    self._owner_is_real_device
                    and self._hooks.is_owner_present is not None
                    and self._owner is not None
                ):
                    present = await self._hooks.is_owner_present(self._owner)
                    now = time.monotonic()
                    if present:
                        self._no_controller_since = None
                    elif self._no_controller_since is None:
                        self._no_controller_since = now
                    elif (
                        self._screensaver_enabled
                        and not self._idle
                        and now - self._no_controller_since >= self._idle_after_s
                    ):
                        # Owner absent, game still running (the process-exit
                        # check above already confirmed that this tick) --
                        # go idle (screensaver + TV standby) instead of
                        # tearing down: the game is left running and a
                        # reconnect should resume instantly, not redo the
                        # whole desk->couch activation. `not self._idle`
                        # guards this from re-firing every poll tick once
                        # already idle; the loop keeps running regardless so
                        # the game exiting while idle still tears down
                        # normally via the check above.
                        headline(
                            logger,
                            "state_machine[%s]: owner absent %ss (game still running) -> couch idle (screensaver + TV standby)",
                            owner, self._idle_after_s,
                        )
                        if self._hooks.enter_couch_idle is not None:
                            await self._hooks.enter_couch_idle()
                        self._idle = True
        except asyncio.CancelledError:
            return

    def _spawn_task(self, name: str, coro: Awaitable) -> None:
        self._cancel_task(name)
        self._tasks[name] = supervise(name, coro, self._health)

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
