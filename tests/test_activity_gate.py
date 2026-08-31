"""Direct regression coverage for three rounds of live-testing findings:

1. (2026-08-21) A controller already producing idle presence data at
   daemon startup triggered couch mode with nobody touching it -- the
   startup grace window must withhold that.
2. (2026-08-21) A first attempt required a genuine evdev button press
   before trusting ANY connect, for the device's whole lifetime under the
   daemon -- live testing showed that's stricter than wanted: a
   deliberate power-on after the startup window (or any reconnect the
   daemon has directly witnessed a prior disconnect for) must be trusted
   immediately, no button press required.
3. (2026-08-22) The original fix's "wait out the grace window, then trust
   it anyway" fallback was itself the same bug via a different path:
   restarting the daemon while a controller happened to still be
   connected from an unrelated earlier session fired couch mode once the
   window elapsed. A device whose first-ever signal arrives inside the
   ambiguous window must now stay untrusted indefinitely, not just until
   the window passes -- only a genuinely witnessed disconnect re-arms
   trust.
"""
import asyncio

import pytest

from joystick_notify.activity_gate import ActivityGate
from joystick_notify.debounce import DeviceEvent, StableKind


class FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def connected(device_id: str) -> DeviceEvent:
    return DeviceEvent(device_id=device_id, kind=StableKind.CONNECTED)


def disconnected(device_id: str) -> DeviceEvent:
    return DeviceEvent(device_id=device_id, kind=StableKind.DISCONNECTED)


async def _emit_sink(forwarded):
    async def emit(event):
        forwarded.append(event)

    return emit


@pytest.mark.asyncio
async def test_connect_within_startup_grace_window_is_withheld():
    forwarded = []
    clock = FakeClock()
    gate = ActivityGate(await _emit_sink(forwarded), startup_grace_s=10.0, clock=clock, system_uptime_s=lambda: 99999.0)

    await gate.handle(connected("dev1"))
    assert forwarded == []  # ambiguous -- could be stale carryover state
    await gate.aclose()


@pytest.mark.asyncio
async def test_connect_never_forwarded_once_grace_window_elapses_while_still_connected():
    # Direct regression test for the 2026-08-22 incident: this used to
    # trust the connect once the window passed ("wait and see, then trust
    # anyway"). It must now stay withheld indefinitely -- a still-connected
    # device with no witnessed disconnect is treated as stale carryover,
    # not a real trigger, no matter how long the daemon's been running.
    forwarded = []
    clock = FakeClock()
    gate = ActivityGate(await _emit_sink(forwarded), startup_grace_s=0.05, clock=clock, system_uptime_s=lambda: 99999.0)

    await gate.handle(connected("dev1"))
    assert forwarded == []
    await asyncio.sleep(0.1)  # real sleep so the internally-scheduled wait actually elapses
    assert forwarded == []
    assert "dev1" not in gate._pending
    await gate.aclose()


@pytest.mark.asyncio
async def test_witnessed_disconnect_after_grace_window_elapsed_re_arms_trust():
    # The recovery path: even after a device has been given up on as
    # stale, a genuinely witnessed disconnect (a real power-cycle) must
    # still unlock trust for its next connect -- the "give up" is about
    # the specific held connect, not a permanent ban on the device_id.
    forwarded = []
    clock = FakeClock()
    gate = ActivityGate(await _emit_sink(forwarded), startup_grace_s=0.05, clock=clock, system_uptime_s=lambda: 99999.0)

    await gate.handle(connected("dev1"))
    await asyncio.sleep(0.1)  # grace window elapses, connect given up on
    assert forwarded == []

    await gate.handle(disconnected("dev1"))  # genuine power-cycle, witnessed
    await gate.handle(connected("dev1"))

    connects = [e for e in forwarded if e.kind == StableKind.CONNECTED]
    assert len(connects) == 1
    await gate.aclose()


@pytest.mark.asyncio
async def test_disconnect_within_grace_window_cancels_pending_and_never_forwards_connect():
    forwarded = []
    clock = FakeClock()
    gate = ActivityGate(await _emit_sink(forwarded), startup_grace_s=10.0, clock=clock, system_uptime_s=lambda: 99999.0)

    await gate.handle(connected("dev1"))
    await gate.handle(disconnected("dev1"))
    await asyncio.sleep(0.02)

    kinds = [e.kind for e in forwarded]
    assert StableKind.CONNECTED not in kinds
    assert StableKind.DISCONNECTED in kinds  # disconnect always flows through
    await gate.aclose()


@pytest.mark.asyncio
async def test_connect_after_startup_grace_window_trusted_immediately():
    # Direct regression test for the "power on should be enough" feedback:
    # once the daemon has been running a while, a fresh connect must not
    # require anything extra.
    forwarded = []
    clock = FakeClock()
    gate = ActivityGate(await _emit_sink(forwarded), startup_grace_s=10.0, clock=clock, system_uptime_s=lambda: 99999.0)

    clock.advance(15.0)  # well past the startup window
    await gate.handle(connected("dev1"))

    assert len(forwarded) == 1
    assert forwarded[0].device_id == "dev1"
    await gate.aclose()


@pytest.mark.asyncio
async def test_reconnect_after_witnessed_disconnect_trusted_immediately_even_within_grace_window():
    # Once the daemon has directly seen this device disconnect, a later
    # connect is an unambiguous, freshly-witnessed transition -- no need
    # to wait out the startup grace window again.
    forwarded = []
    clock = FakeClock()
    gate = ActivityGate(await _emit_sink(forwarded), startup_grace_s=10.0, clock=clock, system_uptime_s=lambda: 99999.0)

    await gate.handle(disconnected("dev1"))  # daemon witnesses it absent
    await gate.handle(connected("dev1"))  # still within the startup window

    connects = [e for e in forwarded if e.kind == StableKind.CONNECTED]
    assert len(connects) == 1
    await gate.aclose()


@pytest.mark.asyncio
async def test_independent_devices_gated_independently():
    forwarded = []
    clock = FakeClock()
    gate = ActivityGate(await _emit_sink(forwarded), startup_grace_s=10.0, clock=clock, system_uptime_s=lambda: 99999.0)

    await gate.handle(disconnected("dev2"))  # dev2 now has witnessed history, dev1 does not
    await gate.handle(connected("dev1"))  # no history, within grace -- withheld
    await gate.handle(connected("dev2"))  # witnessed history -- trusted immediately

    connect_ids = [e.device_id for e in forwarded if e.kind == StableKind.CONNECTED]
    assert connect_ids == ["dev2"]
    await gate.aclose()


@pytest.mark.asyncio
async def test_connect_within_startup_grace_window_trusted_immediately_on_fresh_boot():
    # Direct regression test for the 2026-08-29 incident: a full OS
    # reboot with the controller already powered on -- arguably the most
    # common real way this daemon gets used -- silently did nothing,
    # because it hit the exact same indefinite-hold path built for a
    # DAEMON restart on an already-running system. A fresh boot has no
    # possible stale session to protect against at all.
    forwarded = []
    clock = FakeClock()
    gate = ActivityGate(
        await _emit_sink(forwarded),
        startup_grace_s=10.0,
        clock=clock,
        system_uptime_s=lambda: 5.0,  # well under the fresh-boot threshold
    )

    await gate.handle(connected("dev1"))

    assert len(forwarded) == 1
    assert forwarded[0].device_id == "dev1"
    await gate.aclose()


@pytest.mark.asyncio
async def test_connect_within_startup_grace_window_still_withheld_when_system_uptime_high():
    # The original 2026-08-21/2026-08-22 protection must be unaffected
    # when this ISN'T a fresh boot -- i.e. the daemon itself restarted
    # (redeploy, crash) on a system that's been running a while.
    forwarded = []
    clock = FakeClock()
    gate = ActivityGate(
        await _emit_sink(forwarded),
        startup_grace_s=10.0,
        clock=clock,
        system_uptime_s=lambda: 99999.0,  # system's been up a long time
    )

    await gate.handle(connected("dev1"))

    assert forwarded == []
    await gate.aclose()


@pytest.mark.asyncio
async def test_aclose_cancels_pending_waits_without_forwarding():
    forwarded = []
    clock = FakeClock()
    gate = ActivityGate(await _emit_sink(forwarded), startup_grace_s=10.0, clock=clock, system_uptime_s=lambda: 99999.0)

    await gate.handle(connected("dev1"))
    await gate.aclose()
    await asyncio.sleep(0.02)
    assert forwarded == []
