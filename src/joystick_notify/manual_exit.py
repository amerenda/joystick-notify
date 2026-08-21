"""Watches the current couch-mode owner's raw evdev button stream for a
manual "exit couch mode" shortcut — a way back to desk that doesn't depend
on Steam/the launched game noticing anything (unresponsive, mid-loading-
screen, whatever) and is independent of the disconnect/process-exit
teardown paths in state_machine.py.

Deliberately does NOT touch the launched process: the watcher only ever
calls StateMachine.force_exit_to_desk(), which reuses the exact same
_transition(Mode.DESK) machinery as every other teardown trigger —
activate_desk() never touches the launcher/game at all, so this can't stop
Steam or the game, only the couch-mode side effects (CEC/display/audio/
screen-lock).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from .devices.detect import find_evdev_path_for_device
from .event_log import headline
from .health import Health
from .supervisor import supervise

logger = logging.getLogger(__name__)

# Guide/Home/PS/Xbox button — present on nearly every modern gamepad
# (Xbox, PlayStation, Switch Pro, Steam Controller/Deck, 8BitDo), unlike
# any single face/shoulder button, which makes it the least likely to
# collide with normal in-game use while couch mode is active.
DEFAULT_BUTTON = "BTN_MODE"
DEFAULT_HOLD_SECONDS = 3.0


class ManualExitWatcher:
    def __init__(
        self,
        on_exit: Callable[[], Awaitable[None]],
        health: Health | None = None,
        *,
        button: str = DEFAULT_BUTTON,
        hold_seconds: float = DEFAULT_HOLD_SECONDS,
    ) -> None:
        self._on_exit = on_exit
        self._health = health
        self._button = button
        self._hold_seconds = hold_seconds
        self._task: asyncio.Task | None = None

    async def start(self, device_id: str) -> None:
        await self.stop()
        path = find_evdev_path_for_device(device_id)
        if path is None:
            logger.debug("manual_exit: no evdev node found for %s, shortcut unavailable this session", device_id)
            return
        coro = self._watch(path)
        if self._health is not None:
            self._task = supervise("manual_exit", coro, self._health)
        else:
            self._task = asyncio.ensure_future(coro)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _watch(self, path: str) -> None:
        try:
            import evdev
        except ImportError:
            logger.debug("manual_exit: python-evdev not installed, shortcut unavailable")
            return
        try:
            device = evdev.InputDevice(path)
        except OSError as e:
            logger.debug("manual_exit: could not open %s: %s", path, e)
            return
        button_code = getattr(evdev.ecodes, self._button, None)
        if button_code is None:
            logger.warning("manual_exit: unknown button code %r, shortcut disabled", self._button)
            device.close()
            return

        hold_task: asyncio.Task | None = None

        async def fire_after_hold() -> None:
            try:
                await asyncio.sleep(self._hold_seconds)
            except asyncio.CancelledError:
                return
            headline(
                logger,
                "manual_exit: %s held %.1fs -> forcing exit to desk (launched process left untouched)",
                self._button, self._hold_seconds,
            )
            await self._on_exit()

        try:
            async for event in device.async_read_loop():
                if event.type != evdev.ecodes.EV_KEY or event.code != button_code:
                    continue
                if event.value == 1 and hold_task is None:
                    if self._health is not None:
                        hold_task = supervise("manual_exit_hold", fire_after_hold(), self._health)
                    else:
                        hold_task = asyncio.ensure_future(fire_after_hold())
                elif event.value == 0 and hold_task is not None:
                    hold_task.cancel()
                    hold_task = None
        except asyncio.CancelledError:
            pass
        except OSError:
            logger.debug("manual_exit: lost evdev node %s (device disconnected)", path)
        finally:
            if hold_task is not None:
                hold_task.cancel()
            device.close()
