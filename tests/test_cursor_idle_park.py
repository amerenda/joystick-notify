import asyncio

import pytest

from joystick_notify import cursor_idle_park
from joystick_notify.config.schema import CursorConfig
from joystick_notify.health import Health


class _FakeEvent:
    def __init__(self):
        self.type = 0
        self.code = 0
        self.value = 0


def _fake_device_cls(schedule):
    """schedule: list of delays (seconds) to sleep before yielding one fake
    event each -- an empty list means the device produces no activity at
    all (blocks forever, same as a real idle device)."""

    class _FakeInputDevice:
        def __init__(self, path):
            self.path = path
            self.closed = False

        async def async_read_loop(self):
            for delay in schedule:
                await asyncio.sleep(delay)
                yield _FakeEvent()
            await asyncio.Event().wait()

        def close(self):
            self.closed = True

    return _FakeInputDevice


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch):
    # Production polls every 2s -- too slow for tests. Every test in this
    # file uses idle_park_delay_s in the tens-of-milliseconds range, so the
    # poll loop needs to be at least that fine-grained to observe it.
    monkeypatch.setattr(cursor_idle_park, "POLL_INTERVAL_S", 0.01)


@pytest.mark.asyncio
async def test_parks_after_idle_delay_with_no_activity(monkeypatch, tmp_path):
    import evdev

    monkeypatch.setattr(cursor_idle_park, "find_evdev_path_for_device", lambda device_id: "/dev/input/event7")
    monkeypatch.setattr(evdev, "InputDevice", _fake_device_cls([]))

    parked = []

    async def fake_park(health):
        parked.append(health)

    monkeypatch.setattr(cursor_idle_park, "park_to_corner", fake_park)

    config = CursorConfig(idle_park_enabled=True, idle_park_delay_s=0.05)
    health = Health(path=tmp_path / "health.json")
    watcher = cursor_idle_park.CursorIdleParkWatcher(config, health)
    await watcher.start("dev1")
    await asyncio.sleep(0.15)

    assert parked == [health]
    await watcher.stop()


@pytest.mark.asyncio
async def test_does_not_park_before_idle_delay_elapses(monkeypatch, tmp_path):
    import evdev

    monkeypatch.setattr(cursor_idle_park, "find_evdev_path_for_device", lambda device_id: "/dev/input/event7")
    monkeypatch.setattr(evdev, "InputDevice", _fake_device_cls([]))

    parked = []

    async def fake_park(health):
        parked.append(health)

    monkeypatch.setattr(cursor_idle_park, "park_to_corner", fake_park)

    config = CursorConfig(idle_park_enabled=True, idle_park_delay_s=5.0)
    health = Health(path=tmp_path / "health.json")
    watcher = cursor_idle_park.CursorIdleParkWatcher(config, health)
    await watcher.start("dev1")
    await asyncio.sleep(0.1)

    assert parked == []
    await watcher.stop()


@pytest.mark.asyncio
async def test_activity_resets_the_idle_timer_and_can_park_again_later(monkeypatch, tmp_path):
    import evdev

    # One event at 0.03s resets the clock; with a 0.05s delay, the first
    # idle window (0 -> 0.05s) would otherwise have fired without it.
    monkeypatch.setattr(cursor_idle_park, "find_evdev_path_for_device", lambda device_id: "/dev/input/event7")
    monkeypatch.setattr(evdev, "InputDevice", _fake_device_cls([0.03]))

    parked = []

    async def fake_park(health):
        parked.append(health)

    monkeypatch.setattr(cursor_idle_park, "park_to_corner", fake_park)

    config = CursorConfig(idle_park_enabled=True, idle_park_delay_s=0.05)
    health = Health(path=tmp_path / "health.json")
    watcher = cursor_idle_park.CursorIdleParkWatcher(config, health)
    await watcher.start("dev1")

    # Still inside the reset window -- must not have parked yet.
    await asyncio.sleep(0.06)
    assert parked == []

    # Now well past idle_park_delay_s since the one reset at 0.03s.
    await asyncio.sleep(0.06)
    assert parked == [health]
    await watcher.stop()


@pytest.mark.asyncio
async def test_start_is_noop_when_disabled(monkeypatch):
    calls = []
    monkeypatch.setattr(cursor_idle_park, "find_evdev_path_for_device", lambda device_id: calls.append(device_id) or "/dev/input/event7")

    config = CursorConfig(idle_park_enabled=False, idle_park_delay_s=0.05)
    watcher = cursor_idle_park.CursorIdleParkWatcher(config, None)
    await watcher.start("dev1")

    assert calls == []
    assert watcher._task is None
    await watcher.stop()


@pytest.mark.asyncio
async def test_start_is_noop_when_no_evdev_node_found(monkeypatch):
    monkeypatch.setattr(cursor_idle_park, "find_evdev_path_for_device", lambda device_id: None)

    config = CursorConfig(idle_park_enabled=True, idle_park_delay_s=0.05)
    watcher = cursor_idle_park.CursorIdleParkWatcher(config, None)
    await watcher.start("dev1")

    assert watcher._task is None
    await watcher.stop()


@pytest.mark.asyncio
async def test_stop_is_idempotent(monkeypatch):
    import evdev

    monkeypatch.setattr(cursor_idle_park, "find_evdev_path_for_device", lambda device_id: "/dev/input/event7")
    monkeypatch.setattr(evdev, "InputDevice", _fake_device_cls([]))

    config = CursorConfig(idle_park_enabled=True, idle_park_delay_s=5.0)
    watcher = cursor_idle_park.CursorIdleParkWatcher(config, None)
    await watcher.start("dev1")
    await watcher.stop()
    assert watcher._task is None
    await watcher.stop()  # must not raise
    assert watcher._task is None


@pytest.mark.asyncio
async def test_park_to_corner_runs_ydotool_with_large_relative_delta(monkeypatch, tmp_path):
    calls = []

    async def fake_run(args, timeout=cursor_idle_park.YDOTOOL_TIMEOUT_S):
        calls.append(args)
        return 0, ""

    monkeypatch.setattr(cursor_idle_park, "_run_ydotool", fake_run)

    health = Health(path=tmp_path / "health.json")
    await cursor_idle_park.park_to_corner(health)

    assert calls == [["mousemove", "-x", str(cursor_idle_park.PARK_DELTA), "-y", str(cursor_idle_park.PARK_DELTA)]]


@pytest.mark.asyncio
async def test_park_to_corner_reports_degraded_on_failure(monkeypatch, tmp_path):
    async def fake_run(args, timeout=cursor_idle_park.YDOTOOL_TIMEOUT_S):
        return -1, "ydotool: not found"

    monkeypatch.setattr(cursor_idle_park, "_run_ydotool", fake_run)

    health = Health(path=tmp_path / "health.json")
    await cursor_idle_park.park_to_corner(health)

    status = health.get("cursor_idle_park")
    assert status is not None
    assert status.status.value == "degraded"


@pytest.mark.asyncio
async def test_park_to_corner_tolerates_no_health_object(monkeypatch):
    async def fake_run(args, timeout=cursor_idle_park.YDOTOOL_TIMEOUT_S):
        return 0, ""

    monkeypatch.setattr(cursor_idle_park, "_run_ydotool", fake_run)

    await cursor_idle_park.park_to_corner(None)  # must not raise
