import asyncio

import pytest

from joystick_notify import manual_exit


class _FakeEvent:
    def __init__(self, type_, code, value):
        self.type = type_
        self.code = code
        self.value = value


def _fake_device_cls(events):
    class _FakeInputDevice:
        def __init__(self, path):
            self.path = path
            self.closed = False

        async def async_read_loop(self):
            for ev in events:
                yield ev
            # Replayed events exhausted -- block "open" until cancelled,
            # same as a real device with no further input.
            await asyncio.Event().wait()

        def close(self):
            self.closed = True

    return _FakeInputDevice


def _press(evdev, name):
    return _FakeEvent(evdev.ecodes.EV_KEY, getattr(evdev.ecodes, name), 1)


def _release(evdev, name):
    return _FakeEvent(evdev.ecodes.EV_KEY, getattr(evdev.ecodes, name), 0)


@pytest.mark.asyncio
async def test_full_combo_held_past_threshold_fires_on_exit(monkeypatch):
    import evdev

    monkeypatch.setattr(manual_exit, "find_evdev_path_for_device", lambda device_id: "/dev/input/event7")
    events = [_press(evdev, "BTN_TL"), _press(evdev, "BTN_TR"), _press(evdev, "BTN_EAST")]
    monkeypatch.setattr(evdev, "InputDevice", _fake_device_cls(events))

    fired = []

    async def on_exit():
        fired.append(True)

    watcher = manual_exit.ManualExitWatcher(on_exit, hold_seconds=0.02)
    await watcher.start("dev1")
    await asyncio.sleep(0.08)

    assert fired == [True]
    await watcher.stop()


@pytest.mark.asyncio
async def test_partial_combo_does_not_fire(monkeypatch):
    # Only two of the three default buttons (L1 + R1, no B) -- must not fire.
    import evdev

    monkeypatch.setattr(manual_exit, "find_evdev_path_for_device", lambda device_id: "/dev/input/event7")
    events = [_press(evdev, "BTN_TL"), _press(evdev, "BTN_TR")]
    monkeypatch.setattr(evdev, "InputDevice", _fake_device_cls(events))

    fired = []

    async def on_exit():
        fired.append(True)

    watcher = manual_exit.ManualExitWatcher(on_exit, hold_seconds=0.02)
    await watcher.start("dev1")
    await asyncio.sleep(0.08)

    assert fired == []
    await watcher.stop()


@pytest.mark.asyncio
async def test_releasing_any_one_button_before_threshold_cancels(monkeypatch):
    # All three pressed, but R1 releases before the hold threshold -- must
    # not fire even though the other two are still held.
    import evdev

    monkeypatch.setattr(manual_exit, "find_evdev_path_for_device", lambda device_id: "/dev/input/event7")
    events = [
        _press(evdev, "BTN_TL"), _press(evdev, "BTN_TR"), _press(evdev, "BTN_EAST"),
        _release(evdev, "BTN_TR"),
    ]
    monkeypatch.setattr(evdev, "InputDevice", _fake_device_cls(events))

    fired = []

    async def on_exit():
        fired.append(True)

    watcher = manual_exit.ManualExitWatcher(on_exit, hold_seconds=0.05)
    await watcher.start("dev1")
    await asyncio.sleep(0.1)

    assert fired == []
    await watcher.stop()


@pytest.mark.asyncio
async def test_custom_combo_override(monkeypatch):
    # A custom `buttons=` override (e.g. a two-button combo) must be
    # respected instead of the three-button default.
    import evdev

    monkeypatch.setattr(manual_exit, "find_evdev_path_for_device", lambda device_id: "/dev/input/event7")
    events = [_press(evdev, "BTN_SELECT"), _press(evdev, "BTN_START")]
    monkeypatch.setattr(evdev, "InputDevice", _fake_device_cls(events))

    fired = []

    async def on_exit():
        fired.append(True)

    watcher = manual_exit.ManualExitWatcher(on_exit, buttons=["BTN_SELECT", "BTN_START"], hold_seconds=0.02)
    await watcher.start("dev1")
    await asyncio.sleep(0.08)

    assert fired == [True]
    await watcher.stop()


@pytest.mark.asyncio
async def test_unrelated_button_is_ignored(monkeypatch):
    import evdev

    monkeypatch.setattr(manual_exit, "find_evdev_path_for_device", lambda device_id: "/dev/input/event7")
    other_button = _FakeEvent(evdev.ecodes.EV_KEY, evdev.ecodes.BTN_SOUTH, 1)
    monkeypatch.setattr(evdev, "InputDevice", _fake_device_cls([other_button]))

    fired = []

    async def on_exit():
        fired.append(True)

    watcher = manual_exit.ManualExitWatcher(on_exit, hold_seconds=0.02)
    await watcher.start("dev1")
    await asyncio.sleep(0.08)

    assert fired == []
    await watcher.stop()


@pytest.mark.asyncio
async def test_start_with_no_evdev_path_does_nothing(monkeypatch):
    monkeypatch.setattr(manual_exit, "find_evdev_path_for_device", lambda device_id: None)

    async def on_exit():
        pass

    watcher = manual_exit.ManualExitWatcher(on_exit)
    await watcher.start("dev1")

    assert watcher._task is None
    await watcher.stop()  # no-op, must not raise


@pytest.mark.asyncio
async def test_stop_closes_device_and_cancels_pending_hold(monkeypatch):
    import evdev

    monkeypatch.setattr(manual_exit, "find_evdev_path_for_device", lambda device_id: "/dev/input/event7")
    events = [_press(evdev, "BTN_TL"), _press(evdev, "BTN_TR"), _press(evdev, "BTN_EAST")]
    monkeypatch.setattr(evdev, "InputDevice", _fake_device_cls(events))

    fired = []

    async def on_exit():
        fired.append(True)

    watcher = manual_exit.ManualExitWatcher(on_exit, hold_seconds=10.0)
    await watcher.start("dev1")
    await asyncio.sleep(0.02)  # let the presses register and the hold timer start
    await watcher.stop()
    await asyncio.sleep(0.02)

    assert fired == []  # cancelled well before the 10s threshold
