import asyncio
from pathlib import Path

from joystick_notify.config import store as config_store
from joystick_notify.config.schema import JoystickNotifyConfig
from joystick_notify.daemon import _forward_to_state_machine, check_startup_health, run_doctor
from joystick_notify.debounce import DeviceEvent, StableKind
from joystick_notify.health import Health, Status


def test_check_startup_health_fails_when_binaries_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    config = JoystickNotifyConfig()
    health = Health(path=Path(tmp_path) / "health.json")
    ok = check_startup_health(config, health)
    assert ok is False
    assert health.get("deps").status == Status.FAILED


def test_check_startup_health_ok_when_binaries_present_and_cec_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    config = JoystickNotifyConfig()
    config.cec.enabled = False
    health = Health(path=Path(tmp_path) / "health.json")
    ok = check_startup_health(config, health)
    assert ok is True
    assert health.get("cec").status == Status.OK
    assert health.get("cec").reason == "CEC disabled in config"


def test_check_startup_health_fails_when_cec_enabled_but_no_adapter(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr("joystick_notify.daemon.cec_discover.discover_adapters", lambda: [])
    config = JoystickNotifyConfig()
    config.cec.enabled = True
    health = Health(path=Path(tmp_path) / "health.json")
    ok = check_startup_health(config, health)
    assert ok is False
    assert health.get("cec").status == Status.FAILED


def test_check_startup_health_degrades_when_unconfigured(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    config = JoystickNotifyConfig()
    config.configured = False
    health = Health(path=Path(tmp_path) / "health.json")
    check_startup_health(config, health)
    assert health.get("wizard").status == Status.DEGRADED


def test_run_doctor_returns_nonzero_on_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("shutil.which", lambda name: None)
    config = JoystickNotifyConfig()
    health = Health(path=Path(tmp_path) / "health.json")
    rc = run_doctor(config, health)
    assert rc == 1
    out = capsys.readouterr().out
    assert "FAIL" in out


def test_run_doctor_returns_zero_on_success(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    config = JoystickNotifyConfig()
    config.cec.enabled = False
    health = Health(path=Path(tmp_path) / "health.json")
    rc = run_doctor(config, health)
    assert rc == 0


class _FakeStateMachine:
    def __init__(self):
        self.events = []

    async def handle_device_event(self, event):
        self.events.append(event)


def test_forward_to_state_machine_skips_event_when_auto_switch_disabled(tmp_path):
    config_path = Path(tmp_path) / "config.toml"
    config = JoystickNotifyConfig()
    config.auto_switch_enabled = False
    config_store.save(config, config_path)

    sm = _FakeStateMachine()
    event = DeviceEvent(device_id="dev1", kind=StableKind.CONNECTED)
    asyncio.run(_forward_to_state_machine(sm, event, config_path))

    assert sm.events == []


def test_forward_to_state_machine_forwards_event_when_auto_switch_enabled(tmp_path):
    config_path = Path(tmp_path) / "config.toml"
    config = JoystickNotifyConfig()
    config.auto_switch_enabled = True
    config_store.save(config, config_path)

    sm = _FakeStateMachine()
    event = DeviceEvent(device_id="dev1", kind=StableKind.CONNECTED)
    asyncio.run(_forward_to_state_machine(sm, event, config_path))

    assert sm.events == [event]


def test_forward_to_state_machine_reflects_toggle_change_on_the_very_next_event(tmp_path):
    # The whole point of re-reading config.toml per-event instead of a
    # value cached at daemon startup: a toggle from the tray or the wizard
    # (two separate processes with no shared memory) must apply on the
    # very next controller event, not require a daemon restart.
    config_path = Path(tmp_path) / "config.toml"
    config = JoystickNotifyConfig()
    config.auto_switch_enabled = True
    config_store.save(config, config_path)

    sm = _FakeStateMachine()
    event = DeviceEvent(device_id="dev1", kind=StableKind.CONNECTED)
    asyncio.run(_forward_to_state_machine(sm, event, config_path))
    assert len(sm.events) == 1

    config.auto_switch_enabled = False
    config_store.save(config, config_path)
    asyncio.run(_forward_to_state_machine(sm, event, config_path))
    assert len(sm.events) == 1  # not forwarded the second time
