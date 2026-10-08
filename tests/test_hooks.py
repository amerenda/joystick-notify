import logging
import os
import time
from pathlib import Path

import pytest

from joystick_notify.actions import hooks
from joystick_notify.config.schema import JoystickNotifyConfig
from joystick_notify.config.store import load, save
from joystick_notify.debounce import DeviceEvent, StableKind
from joystick_notify.health import Health
from joystick_notify.state_machine import ActionHooks, ActivationError, Mode, StateMachine


def test_clean_commands_drops_blanks_and_non_strings():
    assert hooks.clean_commands(["  a ", "", "   ", 3, None, "b"]) == ["a", "b"]
    assert hooks.clean_commands("not a list") == []


@pytest.mark.asyncio
async def test_phase_runs_in_order_with_env(tmp_path):
    out = tmp_path / "out"
    failed = await hooks.run_phase(
        "poststart",
        [f"echo one >> {out}", f'echo "$JOYSTICK_NOTIFY_PHASE $JOYSTICK_NOTIFY_MODE" >> {out}'],
        "couch",
    )
    assert failed == []
    assert out.read_text().split("\n")[:2] == ["one", "poststart couch"]


@pytest.mark.asyncio
async def test_failure_does_not_stop_later_commands(tmp_path):
    out = tmp_path / "out"
    failed = await hooks.run_phase("preexit", ["exit 3", "nonexistent-cmd-xyz", f"touch {out}"], "couch")
    assert failed == ["exit 3", "nonexistent-cmd-xyz"]
    assert out.exists()


@pytest.mark.asyncio
async def test_timeout_kills_whole_process_group(tmp_path):
    marker = tmp_path / "survivor"
    start = time.monotonic()
    # The child shell spawns a grandchild; both must die, not just the shell.
    failed = await hooks.run_phase("prestart", [f"(sleep 3; touch {marker}) & sleep 30"], "desk", timeout_s=0.5)
    assert failed and time.monotonic() - start < 5
    time.sleep(3.2)
    assert not marker.exists()


def test_config_round_trip_and_bad_values(tmp_path):
    path = Path(tmp_path) / "config.toml"
    cfg = JoystickNotifyConfig()
    cfg.hooks.poststart = ["mouse-hider start", "echo hi"]
    cfg.hooks.timeout_s = 12.0
    save(cfg, path)
    loaded = load(path)
    assert loaded.hooks.poststart == ["mouse-hider start", "echo hi"]
    assert loaded.hooks.prestart == []
    assert loaded.hooks.timeout_s == 12.0

    path.write_text('[hooks]\nprestart = "oops-a-string"\npostexit = ["x", "", 5]\nunknown = 1\n')
    bad = load(path)
    assert bad.hooks.prestart == []
    assert bad.hooks.postexit == ["x"]


def _sm(tmp_path, order, fail_couch=False):
    async def run_hooks(phase):
        order.append(phase)

    async def activate_couch(device_id):
        order.append("activate_couch")
        if fail_couch:
            raise ActivationError("display", "boom")

    async def activate_desk():
        order.append("activate_desk")

    async def launch():
        order.append("launch")

    h = ActionHooks(activate_couch=activate_couch, activate_desk=activate_desk, launch=launch, run_hooks=run_hooks)
    return StateMachine(h, Health(path=Path(tmp_path) / "health.json"), disconnect_grace_s=0.05, poll_interval_s=0.01)


@pytest.mark.asyncio
async def test_phase_ordering_around_transitions(tmp_path):
    order: list[str] = []
    sm = _sm(tmp_path, order)
    await sm.handle_device_event(DeviceEvent(device_id="d", kind=StableKind.CONNECTED))
    assert order == ["prestart", "activate_couch", "launch", "poststart"]
    order.clear()
    await sm.force_exit_to_desk()
    assert order == ["preexit", "activate_desk", "postexit"]
    await sm.aclose()


@pytest.mark.asyncio
async def test_poststart_skipped_when_couch_activation_fails(tmp_path):
    order: list[str] = []
    sm = _sm(tmp_path, order, fail_couch=True)
    await sm.handle_device_event(DeviceEvent(device_id="d", kind=StableKind.CONNECTED))
    assert sm.mode == Mode.DESK
    assert order == ["prestart", "activate_couch"]
    await sm.aclose()


@pytest.mark.asyncio
async def test_crashing_hook_runner_does_not_break_transition(tmp_path):
    order: list[str] = []
    sm = _sm(tmp_path, order)

    async def boom(phase):
        raise RuntimeError("x")

    sm._hooks.run_hooks = boom
    await sm.handle_device_event(DeviceEvent(device_id="d", kind=StableKind.CONNECTED))
    assert sm.mode == Mode.COUCH
    await sm.aclose()
