import asyncio
import tempfile
from pathlib import Path

import pytest

from joystick_notify.debounce import DeviceEvent, StableKind
from joystick_notify.health import Health, Status
from joystick_notify.state_machine import ActionHooks, ActivationError, Mode, StateMachine


def make_health(tmp_path):
    return Health(path=Path(tmp_path) / "health.json")


def make_hooks(**overrides):
    calls = {"couch": 0, "desk": 0, "launch": 0}

    async def activate_couch(device_id):
        calls["couch"] += 1

    async def activate_desk():
        calls["desk"] += 1

    async def launch():
        calls["launch"] += 1

    hooks = ActionHooks(
        activate_couch=overrides.get("activate_couch", activate_couch),
        activate_desk=overrides.get("activate_desk", activate_desk),
        launch=overrides.get("launch", launch),
        is_launch_process_alive=overrides.get("is_launch_process_alive"),
        is_owner_present=overrides.get("is_owner_present"),
        on_reconnect_while_couch=overrides.get("on_reconnect_while_couch"),
    )
    return hooks, calls


@pytest.mark.asyncio
async def test_connect_transitions_to_couch_and_launches(tmp_path):
    health = make_health(tmp_path)
    hooks, calls = make_hooks()
    sm = StateMachine(hooks, health, disconnect_grace_s=0.05, poll_interval_s=0.01)

    await sm.handle_device_event(DeviceEvent(device_id="dev1", kind=StableKind.CONNECTED))

    assert sm.mode == Mode.COUCH
    assert sm.owner == "dev1"
    assert calls["couch"] == 1
    assert calls["launch"] == 1
    await sm.aclose()


@pytest.mark.asyncio
async def test_second_controller_connect_does_not_change_owner(tmp_path):
    health = make_health(tmp_path)
    hooks, calls = make_hooks()
    sm = StateMachine(hooks, health, disconnect_grace_s=0.05, poll_interval_s=0.01)

    await sm.handle_device_event(DeviceEvent(device_id="dev1", kind=StableKind.CONNECTED))
    await sm.handle_device_event(DeviceEvent(device_id="dev2", kind=StableKind.CONNECTED))

    assert sm.owner == "dev1"
    assert calls["couch"] == 1  # only activated once, not re-triggered by dev2
    await sm.aclose()


@pytest.mark.asyncio
async def test_disconnect_after_grace_tears_down(tmp_path):
    health = make_health(tmp_path)
    hooks, calls = make_hooks()
    sm = StateMachine(hooks, health, disconnect_grace_s=0.03, poll_interval_s=0.01)

    await sm.handle_device_event(DeviceEvent(device_id="dev1", kind=StableKind.CONNECTED))
    await sm.handle_device_event(DeviceEvent(device_id="dev1", kind=StableKind.DISCONNECTED))
    assert sm.mode == Mode.COUCH  # not immediate

    await asyncio.sleep(0.08)
    assert sm.mode == Mode.DESK
    assert calls["desk"] == 1
    await sm.aclose()


@pytest.mark.asyncio
async def test_reconnect_within_grace_cancels_teardown(tmp_path):
    """Direct regression test for v1's Pattern A/C: a controller that
    disconnect/reconnects within the grace window (receiver flapping) must
    never trigger a teardown — the pending grace timer has to be a real
    cancelable task, not a fire-and-forget sleep.
    """
    health = make_health(tmp_path)
    hooks, calls = make_hooks()
    sm = StateMachine(hooks, health, disconnect_grace_s=0.05, poll_interval_s=0.01)

    await sm.handle_device_event(DeviceEvent(device_id="dev1", kind=StableKind.CONNECTED))
    await sm.handle_device_event(DeviceEvent(device_id="dev1", kind=StableKind.DISCONNECTED))
    await asyncio.sleep(0.02)
    await sm.handle_device_event(DeviceEvent(device_id="dev1", kind=StableKind.CONNECTED))
    await asyncio.sleep(0.08)

    assert sm.mode == Mode.COUCH
    assert calls["desk"] == 0
    assert calls["couch"] == 1  # not re-activated on reconnect, only on the original desk->couch edge
    await sm.aclose()


@pytest.mark.asyncio
async def test_reconnect_while_couch_fires_hook_without_reactivating_couch(tmp_path):
    """Direct regression test for the manual-exit-shortcut-doesn't-survive-
    reconnect gap: a reconnect while ALREADY in COUCH mode (e.g. a brief
    Bluetooth drop that lands on a renumbered evdev node) must fire
    on_reconnect_while_couch(device_id) exactly once, and must NOT
    re-run activate_couch() -- this isn't a fresh desk->couch activation.
    """
    health = make_health(tmp_path)
    reconnects = []

    async def on_reconnect_while_couch(device_id):
        reconnects.append(device_id)

    hooks, calls = make_hooks(on_reconnect_while_couch=on_reconnect_while_couch)
    sm = StateMachine(hooks, health, disconnect_grace_s=10, poll_interval_s=0.01)

    await sm.handle_device_event(DeviceEvent(device_id="dev1", kind=StableKind.CONNECTED))
    assert calls["couch"] == 1
    assert reconnects == []

    # Still connected (owner never disconnected) -- a second CONNECTED
    # event for the same device_id while already in COUCH is exactly the
    # reconnect-on-a-new-node shape.
    await sm.handle_device_event(DeviceEvent(device_id="dev1", kind=StableKind.CONNECTED))

    assert sm.mode == Mode.COUCH
    assert calls["couch"] == 1  # not re-activated
    assert reconnects == ["dev1"]
    await sm.aclose()


@pytest.mark.asyncio
async def test_launch_process_exit_after_startup_grace_tears_down(tmp_path):
    health = make_health(tmp_path)
    alive_flag = {"alive": True}

    async def is_launch_process_alive():
        return alive_flag["alive"]

    hooks, calls = make_hooks(is_launch_process_alive=is_launch_process_alive)
    sm = StateMachine(
        hooks, health, disconnect_grace_s=10, launch_startup_grace_s=0.02, poll_interval_s=0.01
    )

    await sm.handle_device_event(DeviceEvent(device_id="dev1", kind=StableKind.CONNECTED))
    assert sm.mode == Mode.COUCH

    alive_flag["alive"] = False
    await asyncio.sleep(0.08)

    assert sm.mode == Mode.DESK
    assert calls["desk"] == 1
    await sm.aclose()


@pytest.mark.asyncio
async def test_process_not_visible_during_startup_grace_is_not_treated_as_exit(tmp_path):
    """Direct port of the STEAM_STARTUP_GRACE fix: a cold process start
    takes time to become visible; checking is_launch_process_alive() during
    that window must not cause an immediate, incorrect teardown.
    """
    health = make_health(tmp_path)

    async def is_launch_process_alive():
        return False  # "not visible yet" the whole test

    hooks, calls = make_hooks(is_launch_process_alive=is_launch_process_alive)
    sm = StateMachine(
        hooks, health, disconnect_grace_s=10, launch_startup_grace_s=1.0, poll_interval_s=0.01
    )

    await sm.handle_device_event(DeviceEvent(device_id="dev1", kind=StableKind.CONNECTED))
    await asyncio.sleep(0.05)

    assert sm.mode == Mode.COUCH
    assert calls["desk"] == 0
    await sm.aclose()


@pytest.mark.asyncio
async def test_no_controller_timeout_tears_down_even_with_process_alive(tmp_path):
    health = make_health(tmp_path)

    async def is_launch_process_alive():
        return True

    async def is_owner_present(device_id):
        return False  # owner never comes back

    hooks, calls = make_hooks(
        is_launch_process_alive=is_launch_process_alive, is_owner_present=is_owner_present
    )
    sm = StateMachine(
        hooks,
        health,
        disconnect_grace_s=10,
        launch_startup_grace_s=0.01,
        no_controller_timeout_s=0.03,
        poll_interval_s=0.01,
    )

    await sm.handle_device_event(DeviceEvent(device_id="dev1", kind=StableKind.CONNECTED))
    await asyncio.sleep(0.1)

    assert sm.mode == Mode.DESK
    assert calls["desk"] == 1
    await sm.aclose()


@pytest.mark.asyncio
async def test_activation_error_reports_health_failed_and_stays_desk(tmp_path):
    health = make_health(tmp_path)

    async def failing_activate_couch(device_id):
        raise ActivationError("display", "couch output not found")

    hooks, calls = make_hooks(activate_couch=failing_activate_couch)
    sm = StateMachine(hooks, health, disconnect_grace_s=0.05, poll_interval_s=0.01)

    await sm.handle_device_event(DeviceEvent(device_id="dev1", kind=StableKind.CONNECTED))

    assert sm.mode == Mode.DESK
    status = health.get("display")
    assert status is not None and status.status.value == "failed"
    assert calls["launch"] == 0
    await sm.aclose()


@pytest.mark.asyncio
async def test_process_exit_teardown_completes_despite_transitions_self_cancel_step(tmp_path):
    """Direct regression test for the 2026-08-21 incident: _transition()
    unconditionally cancels self._tasks["owner_watch"] as its first line
    (needed so some *other* trigger tearing down doesn't leave a stale
    watcher running) -- but when the owner_watch loop's own process-exit
    check is what decided to tear down, that cancel targets its own task.
    Real log evidence: "launched process exited -> tearing down to desk"
    logged, then nothing else followed for over a minute -- no CEC/display/
    audio, no "transitioned to desk" -- because activate_desk() got aborted
    mid-flight by the self-cancellation, and only the unrelated disconnect-
    grace path eventually rescued it.
    """
    health = make_health(tmp_path)
    alive_flag = {"alive": True}
    desk_started = asyncio.Event()

    async def is_launch_process_alive():
        return alive_flag["alive"]

    async def slow_activate_desk():
        desk_started.set()
        await asyncio.sleep(0.05)  # stands in for real CEC/display/audio work
        calls["desk"] += 1

    hooks, calls = make_hooks(is_launch_process_alive=is_launch_process_alive, activate_desk=slow_activate_desk)
    sm = StateMachine(hooks, health, disconnect_grace_s=10, launch_startup_grace_s=0.01, poll_interval_s=0.01)

    await sm.handle_device_event(DeviceEvent(device_id="dev1", kind=StableKind.CONNECTED))
    alive_flag["alive"] = False
    await desk_started.wait()
    await asyncio.sleep(0.1)

    assert sm.mode == Mode.DESK
    assert calls["desk"] == 1
    await sm.aclose()


@pytest.mark.asyncio
async def test_reconnect_during_inflight_teardown_does_not_abort_it(tmp_path):
    """Direct regression test for the other half of the same 2026-08-21
    incident class: a controller reconnecting while disconnect_grace's
    teardown is already mid-flight (past the grace sleep, inside
    activate_desk()) must not truncate it. _on_connect() calls
    _cancel_task("disconnect_grace") to cancel a *pending* grace timer --
    but real hardware evidence showed a reconnect arriving mid-teardown cut
    the CEC standby retry loop off partway through instead, because that
    same task identity was still executing the transition itself.
    """
    health = make_health(tmp_path)
    desk_started = asyncio.Event()

    async def slow_activate_desk():
        desk_started.set()
        await asyncio.sleep(0.05)
        calls["desk"] += 1

    hooks, calls = make_hooks(activate_desk=slow_activate_desk)
    sm = StateMachine(hooks, health, disconnect_grace_s=0.02, poll_interval_s=0.01)

    await sm.handle_device_event(DeviceEvent(device_id="dev1", kind=StableKind.CONNECTED))
    await sm.handle_device_event(DeviceEvent(device_id="dev1", kind=StableKind.DISCONNECTED))
    await desk_started.wait()  # teardown is now mid-flight, inside activate_desk()
    await sm.handle_device_event(DeviceEvent(device_id="dev1", kind=StableKind.CONNECTED))  # races it
    await asyncio.sleep(0.1)

    assert calls["desk"] == 1  # in-flight teardown ran to completion, uninterrupted
    await sm.aclose()


@pytest.mark.asyncio
async def test_owner_watch_crash_reports_health_failed_instead_of_dying_silently(tmp_path):
    """Direct regression test for the 2026-08 architecture audit's
    supervision gap: before supervise() was wired into _spawn_task, a bug
    in is_launch_process_alive() (or any owner_watch hook) would propagate
    out of the loop, silently killing the whole background task via
    asyncio's default "Task exception was never retrieved" -- no
    health.failed(), nothing in the event log, and both auto-teardown
    paths (process-exit, no-controller-timeout) permanently disabled for
    the rest of that couch session with zero visible signal anything was
    wrong.
    """
    health = make_health(tmp_path)

    async def broken_is_launch_process_alive():
        raise RuntimeError("boom")

    hooks, calls = make_hooks(is_launch_process_alive=broken_is_launch_process_alive)
    sm = StateMachine(hooks, health, disconnect_grace_s=10, launch_startup_grace_s=0.01, poll_interval_s=0.01)

    await sm.handle_device_event(DeviceEvent(device_id="dev1", kind=StableKind.CONNECTED))
    await asyncio.sleep(0.08)

    status = health.get("owner_watch")
    assert status is not None
    assert status.status == Status.FAILED
    assert "boom" in status.detail
    await sm.aclose()


@pytest.mark.asyncio
async def test_concurrent_transitions_are_serialized(tmp_path):
    """Regression guard for v1's KWin desktop/activity race: two mode-flips
    arriving close together must not interleave activate_couch/activate_desk
    calls — the lock in _transition() must make this impossible by
    construction, not by luck of scheduling.
    """
    health = make_health(tmp_path)
    order = []

    async def activate_couch(device_id):
        order.append("couch_start")
        await asyncio.sleep(0.02)
        order.append("couch_end")

    async def activate_desk():
        order.append("desk_start")
        await asyncio.sleep(0.02)
        order.append("desk_end")

    hooks, calls = make_hooks(activate_couch=activate_couch, activate_desk=activate_desk)
    sm = StateMachine(hooks, health, disconnect_grace_s=0.01, poll_interval_s=0.01)

    await sm.handle_device_event(DeviceEvent(device_id="dev1", kind=StableKind.CONNECTED))
    await sm.handle_device_event(DeviceEvent(device_id="dev1", kind=StableKind.DISCONNECTED))
    await asyncio.sleep(0.1)

    # No interleaving: each activation's start/end must be adjacent.
    for i in range(0, len(order), 2):
        assert order[i].split("_")[0] == order[i + 1].split("_")[0]
    await sm.aclose()
