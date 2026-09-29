"""Parks the (invisible, per cursor.py's theme swap) system pointer in the
bottom-right corner after a period of no controller activity during a
couch session.

Root cause this closes (confirmed live 2026-09-28): Steam's own in-game
overlay (`gameoverlayui`, launched per-game, separate from the Big Picture
process itself) draws its own cursor sprite whenever the pause/overlay UI
takes focus -- unconditionally, not in response to any real pointer-motion
event (a 90s capture across every mouse-capable input device on the box,
spanning two overlay-opens, logged zero KEY/REL/ABS events from anything).
It seeds that sprite's position from wherever the real system pointer
currently sits, which by default is wherever it was last left -- dead
center of the screen, the single worst place for it to reappear mid-game.
There is no way to suppress the overlay's own cursor (it's a closed-source
client drawing its own image, immune to the system cursor theme the same
way cursor.py's hide_theme already is -- see that module's docstring), so
the only real lever is *where* it appears: warping the real pointer out to
a corner during idle stretches means that's where the overlay's cursor
shows up too, out of the way, instead of center-screen.

Warps via `ydotool` (mousemove, a large relative delta that clamps against
the output's real edges regardless of resolution/scale -- confirmed live
2026-09-28, see the PR this shipped in) rather than an absolute move,
since ydotool's absolute mode uses a normalized coordinate space that
doesn't map onto real screen pixels without extra configuration this
doesn't need. Requires `ydotoold` running (ansible-playbooks installs and
enables it as part of roles/joystick-notify, gated on this feature being
enabled -- see that role). Best-effort like cursor.py's theme swap: a
missing/broken ydotool is Health.degraded(), never a reason to fall back
to desk mode.

Mirrors manual_exit.py's start(device_id)/stop() lifecycle and evdev
plumbing (same find_evdev_path_for_device() lookup, same
supervise()-wrapped background task) -- couch-session-scoped, one
instance for the daemon's lifetime, (re)armed against whatever evdev node
the current owner lands on.
"""
from __future__ import annotations

import asyncio
import logging
import time

from .config.schema import CursorConfig
from .devices.detect import find_evdev_path_for_device
from .health import Health
from .supervisor import supervise

logger = logging.getLogger(__name__)

# A relative move this large clamps against any real output's edges
# regardless of its resolution or KWin's current output scale -- see
# devices/detect.py-adjacent notes in the PR this shipped in for why an
# absolute ydotool mousemove isn't used instead.
PARK_DELTA = 20000

POLL_INTERVAL_S = 2.0

YDOTOOL_TIMEOUT_S = 5.0


async def _run_ydotool(args: list[str], timeout: float = YDOTOOL_TIMEOUT_S) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ydotool", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
    except FileNotFoundError as e:
        return -1, str(e)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, "timeout"
    return proc.returncode, out.decode(errors="replace")


async def park_to_corner(health: Health | None = None) -> None:
    rc, out = await _run_ydotool(["mousemove", "-x", str(PARK_DELTA), "-y", str(PARK_DELTA)])
    if health is None:
        return
    if rc == 0:
        health.ok("cursor_idle_park", "parked pointer to corner after idle timeout")
    else:
        health.degraded("cursor_idle_park", f"ydotool mousemove failed: {out}")


class CursorIdleParkWatcher:
    def __init__(self, config: CursorConfig, health: Health | None = None) -> None:
        self._config = config
        self._health = health
        self._task: asyncio.Task | None = None

    async def start(self, device_id: str) -> None:
        await self.stop()
        if not self._config.idle_park_enabled:
            return
        path = find_evdev_path_for_device(device_id)
        if path is None:
            logger.debug(
                "cursor_idle_park: no evdev node found for %s, idle-park unavailable this session", device_id
            )
            return
        coro = self._watch(path)
        if self._health is not None:
            self._task = supervise("cursor_idle_park_watch", coro, self._health)
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
            logger.debug("cursor_idle_park: python-evdev not installed, idle-park unavailable")
            return
        try:
            device = evdev.InputDevice(path)
        except OSError as e:
            logger.debug("cursor_idle_park: could not open %s: %s", path, e)
            return

        state = {"last_activity": time.monotonic(), "parked": False}

        async def _reader() -> None:
            async for _event in device.async_read_loop():
                state["last_activity"] = time.monotonic()
                state["parked"] = False

        async def _idle_poll() -> None:
            while True:
                await asyncio.sleep(POLL_INTERVAL_S)
                idle_for = time.monotonic() - state["last_activity"]
                if idle_for >= self._config.idle_park_delay_s and not state["parked"]:
                    await park_to_corner(self._health)
                    state["parked"] = True

        reader_task = asyncio.ensure_future(_reader())
        idle_task = asyncio.ensure_future(_idle_poll())
        try:
            await asyncio.gather(reader_task, idle_task)
        except asyncio.CancelledError:
            raise
        except OSError:
            logger.debug("cursor_idle_park: lost evdev node %s (device disconnected)", path)
        finally:
            reader_task.cancel()
            idle_task.cancel()
            device.close()
