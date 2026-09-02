"""All D-Bus interaction is injected -- these tests never touch a real
system bus, matching the project's existing pattern of testing
ActionHooks-driven logic with fakes (see test_state_machine.py).
"""
import asyncio

import pytest

from joystick_notify.health import Health, Status
from joystick_notify.shutdown_watcher import ShutdownWatcher


def make_health(tmp_path):
    from pathlib import Path

    return Health(path=Path(tmp_path) / "health.json")


async def _signals(*events):
    for e in events:
        yield e


def _fixed_fd_acquirer(fd=99):
    async def acquire():
        return fd

    return acquire


@pytest.mark.asyncio
async def test_forces_desk_teardown_on_shutdown_signal_when_couch(tmp_path):
    health = make_health(tmp_path)
    calls = {"desk": 0, "released": []}

    async def force_exit_to_desk():
        calls["desk"] += 1

    def release(fd):
        calls["released"].append(fd)

    watcher = ShutdownWatcher(
        force_exit_to_desk,
        is_couch=lambda: True,
        health=health,
        acquire_inhibitor=_fixed_fd_acquirer(42),
        release_inhibitor=release,
        shutdown_signals=lambda: _signals(True),
    )
    await asyncio.wait_for(watcher.run(), timeout=1)

    assert calls["desk"] == 1
    assert calls["released"] == [42]
    assert health.get("shutdown_watcher").status == Status.OK


@pytest.mark.asyncio
async def test_skips_teardown_when_not_couch(tmp_path):
    health = make_health(tmp_path)
    calls = {"desk": 0, "released": []}

    async def force_exit_to_desk():
        calls["desk"] += 1

    watcher = ShutdownWatcher(
        force_exit_to_desk,
        is_couch=lambda: False,
        health=health,
        acquire_inhibitor=_fixed_fd_acquirer(42),
        release_inhibitor=lambda fd: calls["released"].append(fd),
        shutdown_signals=lambda: _signals(True),
    )
    await asyncio.wait_for(watcher.run(), timeout=1)

    assert calls["desk"] == 0  # no session to protect -- never touched
    assert calls["released"] == [42]  # lock still released, shutdown must proceed


@pytest.mark.asyncio
async def test_ignores_cancelled_shutdown_signal_and_waits_for_the_real_one(tmp_path):
    health = make_health(tmp_path)
    calls = {"desk": 0, "released": []}

    async def force_exit_to_desk():
        calls["desk"] += 1

    watcher = ShutdownWatcher(
        force_exit_to_desk,
        is_couch=lambda: True,
        health=health,
        acquire_inhibitor=_fixed_fd_acquirer(42),
        release_inhibitor=lambda fd: calls["released"].append(fd),
        shutdown_signals=lambda: _signals(False, False, True),
    )
    await asyncio.wait_for(watcher.run(), timeout=1)

    assert calls["desk"] == 1  # only reacted to the real (True) signal
    assert calls["released"] == [42]  # released exactly once, after the real signal


@pytest.mark.asyncio
async def test_teardown_timeout_bounds_the_wait_and_still_releases(tmp_path):
    health = make_health(tmp_path)
    calls = {"desk_started": 0, "released": []}

    async def slow_force_exit_to_desk():
        calls["desk_started"] += 1
        await asyncio.sleep(10)  # far longer than the test's teardown_timeout_s

    watcher = ShutdownWatcher(
        slow_force_exit_to_desk,
        is_couch=lambda: True,
        health=health,
        teardown_timeout_s=0.05,
        acquire_inhibitor=_fixed_fd_acquirer(42),
        release_inhibitor=lambda fd: calls["released"].append(fd),
        shutdown_signals=lambda: _signals(True),
    )
    # A bug in the teardown must never hang the caller past the timeout --
    # the outer wait_for here is just a test safety net, well above the
    # watcher's own 0.05s bound.
    await asyncio.wait_for(watcher.run(), timeout=1)

    assert calls["desk_started"] == 1
    assert calls["released"] == [42]  # released even though teardown never finished


@pytest.mark.asyncio
async def test_gives_up_and_reports_degraded_when_no_inhibitor_available(tmp_path):
    health = make_health(tmp_path)
    calls = {"desk": 0, "released": []}

    async def force_exit_to_desk():
        calls["desk"] += 1

    async def no_lock():
        return None

    watcher = ShutdownWatcher(
        force_exit_to_desk,
        is_couch=lambda: True,
        health=health,
        acquire_inhibitor=no_lock,
        release_inhibitor=lambda fd: calls["released"].append(fd),
        shutdown_signals=lambda: _signals(True),
    )
    await asyncio.wait_for(watcher.run(), timeout=1)

    assert calls["desk"] == 0
    assert calls["released"] == []  # never acquired, nothing to release
    assert health.get("shutdown_watcher").status == Status.DEGRADED


@pytest.mark.asyncio
async def test_run_is_cancellable_and_still_releases_the_lock(tmp_path):
    # Ordinary redeploy/service-stop path: the daemon cancels this task
    # directly (no PrepareForShutdown signal ever arrives) -- the fd must
    # not leak.
    health = make_health(tmp_path)
    calls = {"released": []}

    async def never_shuts_down():
        if False:
            yield True  # pragma: no cover -- makes this an async generator that never yields
        await asyncio.Event().wait()

    async def force_exit_to_desk():
        pass

    watcher = ShutdownWatcher(
        force_exit_to_desk,
        is_couch=lambda: True,
        health=health,
        acquire_inhibitor=_fixed_fd_acquirer(42),
        release_inhibitor=lambda fd: calls["released"].append(fd),
        shutdown_signals=never_shuts_down,
    )
    task = asyncio.ensure_future(watcher.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls["released"] == [42]
