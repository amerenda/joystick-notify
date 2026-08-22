import asyncio
from pathlib import Path

import pytest

from joystick_notify.health import Health, Status
from joystick_notify.supervisor import supervise


def make_health(tmp_path):
    return Health(path=Path(tmp_path) / "health.json")


@pytest.mark.asyncio
async def test_supervise_runs_coro_to_completion(tmp_path):
    health = make_health(tmp_path)
    ran = []

    async def ok():
        ran.append(True)

    task = supervise("thing", ok(), health)
    await task

    assert ran == [True]
    assert health.get("thing") is None  # never touched on success


@pytest.mark.asyncio
async def test_supervise_reports_health_failed_on_exception(tmp_path):
    # supervise() returns a real Task wrapping `coro` directly (same as a
    # bare asyncio.ensure_future(coro) would) -- an uncaught exception still
    # surfaces to anyone awaiting the task, exactly as it always did. What's
    # new is the done-callback recording it to Health *regardless* of
    # whether anyone awaits the task at all, which is the actual case that
    # mattered (every real caller stores the task and never awaits it
    # directly).
    health = make_health(tmp_path)

    async def boom():
        raise ValueError("kaboom")

    task = supervise("thing", boom(), health)
    with pytest.raises(ValueError, match="kaboom"):
        await task

    status = health.get("thing")
    assert status is not None
    assert status.status == Status.FAILED
    assert "kaboom" in status.detail


@pytest.mark.asyncio
async def test_supervise_reports_health_failed_even_when_never_awaited(tmp_path):
    # The real-world case: state_machine._spawn_task stores the Task in a
    # dict and never awaits it directly; cec_control's retry task is
    # likewise just held for possible cancellation. The done-callback must
    # fire on its own once the task finishes, without requiring a caller to
    # await it first.
    health = make_health(tmp_path)

    async def boom():
        raise ValueError("kaboom")

    task = supervise("thing", boom(), health)
    # asyncio.wait() blocks until the task is done WITHOUT retrieving its
    # exception (unlike `await task` directly) -- exercises exactly the
    # real-world shape: nothing ever awaits the stored task, but we still
    # need to deterministically wait for it (and its done-callback, which
    # was registered first and therefore runs before wait() returns) to
    # actually finish before asserting.
    await asyncio.wait([task])

    status = health.get("thing")
    assert status is not None
    assert status.status == Status.FAILED
    # Task finished with an exception nobody awaited -- must not print
    # asyncio's default "exception was never retrieved" warning. Retrieve
    # it explicitly so the test itself doesn't trigger that either.
    assert isinstance(task.exception(), ValueError)


@pytest.mark.asyncio
async def test_supervise_propagates_cancellation_normally(tmp_path):
    health = make_health(tmp_path)

    async def forever():
        await asyncio.sleep(10)

    task = supervise("thing", forever(), health)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # A deliberate cancellation is not a crash -- must not be reported failed.
    assert health.get("thing") is None
