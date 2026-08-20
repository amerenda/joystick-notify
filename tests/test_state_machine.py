import asyncio
import tempfile
from pathlib import Path

import pytest

from joystick_notify.debounce import DeviceEvent, StableKind
from joystick_notify.health import Health
from joystick_notify.state_machine import ActionHooks, ActivationError, Mode, StateMachine


def make_health(tmp_path):
    return Health(path=Path(tmp_path) / "health.json")


def make_hooks(**overrides):
    calls = {"couch": 0, "desk": 0, "launch": 0}

    async def activate_couch():
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

    async def failing_activate_couch():
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
async def test_concurrent_transitions_are_serialized(tmp_path):
    """Regression guard for v1's KWin desktop/activity race: two mode-flips
    arriving close together must not interleave activate_couch/activate_desk
    calls — the lock in _transition() must make this impossible by
    construction, not by luck of scheduling.
    """
    health = make_health(tmp_path)
    order = []

    async def activate_couch():
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
