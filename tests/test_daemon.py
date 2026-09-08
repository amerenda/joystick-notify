import asyncio
from pathlib import Path

import pytest

from joystick_notify.config import store as config_store
from joystick_notify.config.schema import JoystickNotifyConfig
from joystick_notify.daemon import _forward_to_state_machine, build_hooks, check_startup_health, main, run_doctor
from joystick_notify.debounce import DeviceEvent, StableKind
from joystick_notify.health import Health, Status
from joystick_notify.manual_exit import ManualExitWatcher
from joystick_notify.wizard.auth import check_bearer_token, load_api_token


def test_main_install_api_token_persists_hash_and_exits(tmp_path, monkeypatch):
    # Out-of-band provisioning path used by ansible's sunshine role --
    # must exit immediately without starting the full daemon.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    rc = main(["--install-api-token", "a-token-from-bws"])

    assert rc == 0
    loaded = load_api_token(tmp_path / "joystick-notify" / "api_token.json")
    assert loaded is not None
    assert check_bearer_token("Bearer a-token-from-bws", loaded) is True


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


@pytest.mark.asyncio
async def test_activate_desk_exits_launched_process_via_builtin_default(tmp_path, monkeypatch):
    from joystick_notify import daemon as daemon_module

    exit_calls = []

    async def fake_exit_launched(preset_or_command, teardown_command):
        exit_calls.append((preset_or_command, teardown_command))

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(daemon_module.launchers, "exit_launched", fake_exit_launched)
    monkeypatch.setattr(daemon_module.display_actions, "activate_desk", _noop)
    monkeypatch.setattr(daemon_module.audio_actions, "activate_desk", _noop)
    monkeypatch.setattr(daemon_module.screen_lock_actions, "activate_desk", _noop)

    config = JoystickNotifyConfig()
    config.on_connect.run = "steam-bigpicture"
    health = Health(path=Path(tmp_path) / "health.json")
    watcher = ManualExitWatcher(lambda: None, health)

    hooks = build_hooks(config, health, watcher, tmp_path / "config.toml")
    await hooks.activate_desk()

    assert exit_calls == [("steam-bigpicture", "")]


@pytest.mark.asyncio
async def test_activate_desk_uses_custom_teardown_command_when_set(tmp_path, monkeypatch):
    from joystick_notify import daemon as daemon_module

    exit_calls = []

    async def fake_exit_launched(preset_or_command, teardown_command):
        exit_calls.append((preset_or_command, teardown_command))

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(daemon_module.launchers, "exit_launched", fake_exit_launched)
    monkeypatch.setattr(daemon_module.display_actions, "activate_desk", _noop)
    monkeypatch.setattr(daemon_module.audio_actions, "activate_desk", _noop)
    monkeypatch.setattr(daemon_module.screen_lock_actions, "activate_desk", _noop)

    config = JoystickNotifyConfig()
    config.on_connect.run = "my-custom-game"
    config.on_connect.teardown_command = "my-custom-game --quit"
    health = Health(path=Path(tmp_path) / "health.json")
    watcher = ManualExitWatcher(lambda: None, health)

    hooks = build_hooks(config, health, watcher, tmp_path / "config.toml")
    await hooks.activate_desk()

    assert exit_calls == [("my-custom-game", "my-custom-game --quit")]


@pytest.mark.asyncio
async def test_activate_desk_skips_exit_launched_when_nothing_configured(tmp_path, monkeypatch):
    from joystick_notify import daemon as daemon_module

    exit_calls = []

    async def fake_exit_launched(preset_or_command, teardown_command):
        exit_calls.append((preset_or_command, teardown_command))

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(daemon_module.launchers, "exit_launched", fake_exit_launched)
    monkeypatch.setattr(daemon_module.display_actions, "activate_desk", _noop)
    monkeypatch.setattr(daemon_module.audio_actions, "activate_desk", _noop)
    monkeypatch.setattr(daemon_module.screen_lock_actions, "activate_desk", _noop)

    config = JoystickNotifyConfig()  # no on_connect.run, no teardown_command
    health = Health(path=Path(tmp_path) / "health.json")
    watcher = ManualExitWatcher(lambda: None, health)

    hooks = build_hooks(config, health, watcher, tmp_path / "config.toml")
    await hooks.activate_desk()

    assert exit_calls == []


@pytest.mark.asyncio
async def test_activate_couch_rereads_cec_enabled_from_disk_not_startup_cache(tmp_path, monkeypatch):
    """Regression test for the reported bug: unchecking "Enable CEC" in the
    wizard and saving updates config.toml, but the daemon's `config` object
    (closed over by build_hooks() at startup) used to keep the value it had
    when the daemon started. Couch activation must reflect the config file
    as it is *right now*, not as it was when build_hooks() was called.
    """
    from joystick_notify import daemon as daemon_module

    async def _noop(*args, **kwargs):
        return None

    cec_calls = []

    async def fake_wake_and_select_input(*args, **kwargs):
        cec_calls.append((args, kwargs))
        return None

    monkeypatch.setattr(daemon_module.screen_lock_actions, "activate_couch", _noop)
    monkeypatch.setattr(daemon_module.display_actions, "activate_couch", _noop)
    monkeypatch.setattr(daemon_module.audio_actions, "activate_couch", _noop)
    monkeypatch.setattr(daemon_module.cursor_actions, "activate_couch", _noop)
    monkeypatch.setattr(daemon_module.cec_control, "wake_and_select_input", fake_wake_and_select_input)

    config_path = tmp_path / "config.toml"

    # Simulate the daemon having started with CEC enabled...
    config = JoystickNotifyConfig()
    config.cec.enabled = True
    config.cec.adapter = "/dev/cec0"
    config.shortcuts.exit_couch_enabled = False
    config_store.save(config, config_path)

    health = Health(path=Path(tmp_path) / "health.json")
    watcher = ManualExitWatcher(lambda: None, health)
    hooks = build_hooks(config, health, watcher, config_path)

    # ...then the wizard saves CEC disabled, through its own separate
    # config object -- the daemon's in-memory `config` above is untouched.
    disk_config = config_store.load(config_path)
    disk_config.cec.enabled = False
    config_store.save(disk_config, config_path)

    await hooks.activate_couch("device-1")

    assert cec_calls == []


@pytest.mark.asyncio
async def test_activate_couch_picks_up_cec_enabled_toggled_on_without_restart(tmp_path, monkeypatch):
    """Mirror of the disable case: CEC enabled via the wizard after daemon
    startup (in-memory config still has it disabled) must take effect on
    the very next couch activation, matching auto_switch_enabled's reload.
    """
    from joystick_notify import daemon as daemon_module

    async def _noop(*args, **kwargs):
        return None

    cec_calls = []

    async def fake_wake_and_select_input(*args, **kwargs):
        cec_calls.append((args, kwargs))
        return None

    monkeypatch.setattr(daemon_module.screen_lock_actions, "activate_couch", _noop)
    monkeypatch.setattr(daemon_module.display_actions, "activate_couch", _noop)
    monkeypatch.setattr(daemon_module.audio_actions, "activate_couch", _noop)
    monkeypatch.setattr(daemon_module.cursor_actions, "activate_couch", _noop)
    monkeypatch.setattr(daemon_module.cec_control, "wake_and_select_input", fake_wake_and_select_input)

    config_path = tmp_path / "config.toml"

    # Daemon started with CEC disabled...
    config = JoystickNotifyConfig()
    config.cec.enabled = False
    config.shortcuts.exit_couch_enabled = False
    config_store.save(config, config_path)

    health = Health(path=Path(tmp_path) / "health.json")
    watcher = ManualExitWatcher(lambda: None, health)
    hooks = build_hooks(config, health, watcher, config_path)

    # ...then the wizard enables it, through its own separate config object.
    disk_config = config_store.load(config_path)
    disk_config.cec.enabled = True
    disk_config.cec.adapter = "/dev/cec0"
    disk_config.cec.active_source_phys_addr = "3.2.0.0"
    config_store.save(disk_config, config_path)

    await hooks.activate_couch("device-1")

    assert len(cec_calls) == 1
    args, kwargs = cec_calls[0]
    assert args[0] == "/dev/cec0"
    assert args[1] == "3.2.0.0"
