"""Direct regression coverage for the 2026-08-21 live-testing finding: a
controller already producing idle presence data at daemon startup
triggered couch mode with nobody touching it. ActivityGate must withhold
a first-time connect until genuine activity is proven, and must never
falsely delay/block a disconnect or an already-proven-active device.
"""
import asyncio

import pytest

from joystick_notify.activity_gate import ActivityGate
from joystick_notify.debounce import DeviceEvent, StableKind


class FakeDetector:
    """Test double: wait_for_activity() blocks until the test explicitly
    fires it via `resolve(device_id)`, or raises CancelledError if the
    gate cancels it first (simulating a disconnect before activity).
    Matches the real EvdevActivityDetector's shape: each call starts a
    fresh wait with no memory of a previous device_id's activity, so a
    second connect after a real disconnect must be resolved again.
    """

    def __init__(self):
        self._pending: dict[str, list[asyncio.Event]] = {}
        self.watched: list[str] = []

    async def wait_for_activity(self, device_id: str) -> None:
        self.watched.append(device_id)
        event = asyncio.Event()
        self._pending.setdefault(device_id, []).append(event)
        await event.wait()

    def resolve(self, device_id: str) -> None:
        for event in self._pending.pop(device_id, []):
            event.set()


def connected(device_id: str) -> DeviceEvent:
    return DeviceEvent(device_id=device_id, kind=StableKind.CONNECTED)


def disconnected(device_id: str) -> DeviceEvent:
    return DeviceEvent(device_id=device_id, kind=StableKind.DISCONNECTED)


@pytest.mark.asyncio
async def test_connect_withheld_until_activity_proven():
    forwarded = []
    detector = FakeDetector()
    gate = ActivityGate(lambda e: forwarded.append(e) or asyncio.sleep(0), detector)

    await gate.handle(connected("dev1"))
    await asyncio.sleep(0.05)
    assert forwarded == []  # not forwarded yet -- no activity proven

    detector.resolve("dev1")
    await asyncio.sleep(0.05)
    assert len(forwarded) == 1
    assert forwarded[0].device_id == "dev1"
    await gate.aclose()


@pytest.mark.asyncio
async def test_disconnect_before_activity_never_forwards_connect():
    # This is the exact false-positive this exists to close: a device
    # present-but-idle that goes away before ever being touched must never
    # have triggered anything downstream.
    forwarded = []
    detector = FakeDetector()
    gate = ActivityGate(lambda e: forwarded.append(e) or asyncio.sleep(0), detector)

    await gate.handle(connected("dev1"))
    await asyncio.sleep(0.02)
    await gate.handle(disconnected("dev1"))
    await asyncio.sleep(0.05)

    kinds = [e.kind for e in forwarded]
    assert StableKind.CONNECTED not in kinds
    assert StableKind.DISCONNECTED in kinds  # disconnect still flows through
    await gate.aclose()


@pytest.mark.asyncio
async def test_disconnect_always_forwarded_immediately_even_never_activated():
    forwarded = []
    detector = FakeDetector()
    gate = ActivityGate(lambda e: forwarded.append(e) or asyncio.sleep(0), detector)

    # Disconnect with no prior connect at all -- must not raise, must forward.
    await gate.handle(disconnected("dev1"))
    assert len(forwarded) == 1
    assert forwarded[0].kind == StableKind.DISCONNECTED
    await gate.aclose()


@pytest.mark.asyncio
async def test_once_activated_presence_alone_is_forwarded_without_regating():
    forwarded = []
    detector = FakeDetector()
    gate = ActivityGate(lambda e: forwarded.append(e) or asyncio.sleep(0), detector)

    await gate.handle(connected("dev1"))
    await asyncio.sleep(0)  # let the spawned wait register before resolving it
    detector.resolve("dev1")
    await asyncio.sleep(0.02)
    assert len(forwarded) == 1

    # A second "connected" for the same, already-activated device (e.g. a
    # redundant signal from a second detector source) must pass straight
    # through -- lenient once active, not re-gated on every event.
    watched_before = list(detector.watched)
    await gate.handle(connected("dev1"))
    assert len(forwarded) == 2
    assert detector.watched == watched_before  # did not start watching again
    await gate.aclose()


@pytest.mark.asyncio
async def test_disconnect_clears_activated_so_next_connect_is_regated():
    forwarded = []
    detector = FakeDetector()
    gate = ActivityGate(lambda e: forwarded.append(e) or asyncio.sleep(0), detector)

    await gate.handle(connected("dev1"))
    await asyncio.sleep(0)  # let the spawned wait register before resolving it
    detector.resolve("dev1")
    await asyncio.sleep(0.02)
    await gate.handle(disconnected("dev1"))
    await asyncio.sleep(0.02)

    # Fresh connect after a real disconnect must prove activity again --
    # trust doesn't persist across an actual disconnect.
    await gate.handle(connected("dev1"))
    await asyncio.sleep(0.02)
    assert len([e for e in forwarded if e.kind == StableKind.CONNECTED]) == 1  # only the first one
    detector.resolve("dev1")
    await asyncio.sleep(0.02)
    assert len([e for e in forwarded if e.kind == StableKind.CONNECTED]) == 2
    await gate.aclose()


@pytest.mark.asyncio
async def test_independent_devices_gated_independently():
    forwarded = []
    detector = FakeDetector()
    gate = ActivityGate(lambda e: forwarded.append(e) or asyncio.sleep(0), detector)

    await gate.handle(connected("dev1"))
    await gate.handle(connected("dev2"))
    await asyncio.sleep(0)  # let both spawned waits register before resolving one
    detector.resolve("dev2")
    await asyncio.sleep(0.02)

    ids = [e.device_id for e in forwarded]
    assert ids == ["dev2"]  # dev1 still withheld, dev2 forwarded
    await gate.aclose()


@pytest.mark.asyncio
async def test_aclose_cancels_all_pending_waits_without_forwarding():
    forwarded = []
    detector = FakeDetector()
    gate = ActivityGate(lambda e: forwarded.append(e) or asyncio.sleep(0), detector)

    await gate.handle(connected("dev1"))
    await gate.aclose()
    await asyncio.sleep(0.02)
    assert forwarded == []
