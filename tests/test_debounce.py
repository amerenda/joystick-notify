import asyncio

import pytest

from joystick_notify.debounce import Debouncer, RawEvent, RawKind, StableKind


class Recorder:
    def __init__(self):
        self.events = []

    async def __call__(self, event):
        self.events.append(event)


@pytest.mark.asyncio
async def test_single_add_emits_after_window():
    rec = Recorder()
    d = Debouncer(rec, default_debounce_ms=20)
    d.feed(RawEvent(device_id="dev1", kind=RawKind.ADD))
    assert rec.events == []
    await asyncio.sleep(0.05)
    assert len(rec.events) == 1
    assert rec.events[0].kind == StableKind.CONNECTED
    await d.aclose()


@pytest.mark.asyncio
async def test_rapid_flapping_is_absorbed_not_emitted():
    """The exact bug class from the v1 audit: a receiver that bounces
    add/remove/add/remove rapidly on power-on must not produce a storm of
    stable events downstream — only the settled final state should emit,
    and only once.
    """
    rec = Recorder()
    d = Debouncer(rec, default_debounce_ms=30)
    for _ in range(10):
        d.feed(RawEvent(device_id="dev1", kind=RawKind.ADD))
        d.feed(RawEvent(device_id="dev1", kind=RawKind.REMOVE))
    d.feed(RawEvent(device_id="dev1", kind=RawKind.ADD))
    await asyncio.sleep(0.06)
    assert len(rec.events) == 1
    assert rec.events[0].kind == StableKind.CONNECTED
    await d.aclose()


@pytest.mark.asyncio
async def test_bounce_back_to_current_stable_state_is_noop():
    rec = Recorder()
    d = Debouncer(rec, default_debounce_ms=15)
    d.feed(RawEvent(device_id="dev1", kind=RawKind.ADD))
    await asyncio.sleep(0.03)
    assert len(rec.events) == 1

    # Already connected; a redundant ADD (e.g. udev "change" re-fired as add)
    # must not schedule or emit anything new.
    d.feed(RawEvent(device_id="dev1", kind=RawKind.ADD))
    await asyncio.sleep(0.03)
    assert len(rec.events) == 1
    await d.aclose()


@pytest.mark.asyncio
async def test_independent_devices_debounce_independently():
    rec = Recorder()
    d = Debouncer(rec, default_debounce_ms=15)
    d.feed(RawEvent(device_id="dev1", kind=RawKind.ADD))
    d.feed(RawEvent(device_id="dev2", kind=RawKind.ADD))
    await asyncio.sleep(0.03)
    ids = {e.device_id for e in rec.events}
    assert ids == {"dev1", "dev2"}
    await d.aclose()


@pytest.mark.asyncio
async def test_per_class_debounce_window():
    rec = Recorder()
    d = Debouncer(rec, default_debounce_ms=200, per_class_debounce_ms={"steam_puck": 10})
    d.feed(RawEvent(device_id="dev1", kind=RawKind.ADD, device_class="steam_puck"))
    await asyncio.sleep(0.03)
    assert len(rec.events) == 1
    await d.aclose()


@pytest.mark.asyncio
async def test_aclose_cancels_pending_without_emitting():
    rec = Recorder()
    d = Debouncer(rec, default_debounce_ms=100)
    d.feed(RawEvent(device_id="dev1", kind=RawKind.ADD))
    await d.aclose()
    await asyncio.sleep(0.15)
    assert rec.events == []
