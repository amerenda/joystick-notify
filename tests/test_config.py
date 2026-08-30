from pathlib import Path

from joystick_notify.config.schema import CustomCommand, JoystickNotifyConfig
from joystick_notify.config.store import load, save


def test_missing_config_returns_defaults_unconfigured(tmp_path):
    cfg = load(Path(tmp_path) / "config.toml")
    assert cfg.configured is False
    assert cfg.cec.enabled is False
    assert cfg.wizard.bind_address == "127.0.0.1"
    # Bypassing the lock screen is a real security tradeoff -- must
    # default off, same treatment as CEC.
    assert cfg.screen_lock.enabled is False
    # Waiting for a reconnect and the idle screensaver are pure safety/
    # convenience defaults, not security tradeoffs -- on by default.
    assert cfg.idle.wait_for_game is True
    assert cfg.idle.screensaver_enabled is True
    # Auto-switch is on by default -- unlike screen-lock bypass, reacting
    # to the controller is the whole point of the app, not a security
    # tradeoff that needs opt-in.
    assert cfg.auto_switch_enabled is True


def test_auto_switch_enabled_round_trips(tmp_path):
    path = Path(tmp_path) / "config.toml"
    cfg = JoystickNotifyConfig()
    cfg.auto_switch_enabled = False
    save(cfg, path)

    loaded = load(path)
    assert loaded.auto_switch_enabled is False


def test_cursor_config_round_trips(tmp_path):
    # Regression test: _from_dict() originally omitted `cursor=` when
    # reconstructing JoystickNotifyConfig, so the dataclass default
    # silently won every load regardless of what was actually saved to
    # disk -- caught before deploy, not by this test failing first.
    path = Path(tmp_path) / "config.toml"
    cfg = JoystickNotifyConfig()
    cfg.cursor.enabled = True
    cfg.cursor.hide_theme = "invisible"
    cfg.cursor.normal_theme = "breeze_cursors"
    save(cfg, path)

    loaded = load(path)
    assert loaded.cursor.enabled is True
    assert loaded.cursor.hide_theme == "invisible"
    assert loaded.cursor.normal_theme == "breeze_cursors"


def test_teardown_command_defaults_empty():
    assert JoystickNotifyConfig().on_connect.teardown_command == ""


def test_teardown_command_round_trips(tmp_path):
    path = Path(tmp_path) / "config.toml"
    cfg = JoystickNotifyConfig()
    cfg.on_connect.teardown_command = "steam -shutdown"
    save(cfg, path)

    loaded = load(path)
    assert loaded.on_connect.teardown_command == "steam -shutdown"


def test_round_trip_preserves_values(tmp_path):
    path = Path(tmp_path) / "config.toml"
    cfg = JoystickNotifyConfig()
    cfg.configured = True
    cfg.display.desk_port = "HDMI-A-2"
    cfg.display.couch_port = "HDMI-A-1"
    cfg.cec.enabled = True
    cfg.cec.standby_targets = [0, 5]
    cfg.on_connect.run = "steam-bigpicture"
    cfg.custom_commands = [
        CustomCommand(name="Play Portal 2", command="steam steam://rungameid/620"),
        CustomCommand(name="Play Half-Life 2", command="steam steam://rungameid/220"),
    ]
    cfg.screen_lock.enabled = True
    cfg.screen_lock.hold_inhibit = False
    cfg.idle.wait_for_game = False
    cfg.idle.screensaver_enabled = False
    cfg.idle.idle_after_s = 45.0

    save(cfg, path)
    loaded = load(path)

    assert loaded.configured is True
    assert loaded.display.desk_port == "HDMI-A-2"
    assert loaded.cec.standby_targets == [0, 5]
    assert loaded.on_connect.run == "steam-bigpicture"
    assert loaded.custom_commands == [
        CustomCommand(name="Play Portal 2", command="steam steam://rungameid/620"),
        CustomCommand(name="Play Half-Life 2", command="steam steam://rungameid/220"),
    ]
    assert loaded.screen_lock.enabled is True
    assert loaded.screen_lock.hold_inhibit is False
    assert loaded.idle.wait_for_game is False
    assert loaded.idle.screensaver_enabled is False
    assert loaded.idle.idle_after_s == 45.0


def test_save_sets_restrictive_permissions(tmp_path):
    path = Path(tmp_path) / "config.toml"
    save(JoystickNotifyConfig(), path)
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600


def test_loading_config_with_removed_field_does_not_raise(tmp_path):
    # Direct regression test: cec.allm_enabled, cec.selfheal_cooldown_s,
    # on_connect.power_on, and on_disconnect were all removed from the
    # schema as dead fields. An old config.toml written before that
    # removal still has these keys on disk -- loading it must silently
    # drop them, not raise TypeError on an unexpected keyword argument.
    path = Path(tmp_path) / "config.toml"
    path.write_text(
        "configured = true\n"
        "\n"
        "[cec]\n"
        "enabled = true\n"
        "allm_enabled = true\n"
        "selfheal_cooldown_s = 120.0\n"
        "\n"
        "[on_connect]\n"
        "run = \"steam-bigpicture\"\n"
        "power_on = [\"cec:tv\"]\n"
        "\n"
        "[on_disconnect]\n"
        "run = \"some-command\"\n"
        "\n"
        "[timing]\n"
        "no_controller_timeout_s = 120.0\n"
    )
    cfg = load(path)
    assert cfg.configured is True
    assert cfg.cec.enabled is True
    assert cfg.on_connect.run == "steam-bigpicture"
    assert cfg.idle.idle_after_s == 120.0  # schema default, unaffected by the stale timing key
