from pathlib import Path

from joystick_notify.config.schema import JoystickNotifyConfig
from joystick_notify.config.store import load, save


def test_missing_config_returns_defaults_unconfigured(tmp_path):
    cfg = load(Path(tmp_path) / "config.toml")
    assert cfg.configured is False
    assert cfg.cec.enabled is False
    assert cfg.wizard.bind_address == "127.0.0.1"
    # Bypassing the lock screen is a real security tradeoff -- must
    # default off, same treatment as CEC.
    assert cfg.screen_lock.enabled is False


def test_round_trip_preserves_values(tmp_path):
    path = Path(tmp_path) / "config.toml"
    cfg = JoystickNotifyConfig()
    cfg.configured = True
    cfg.display.desk_port = "HDMI-A-2"
    cfg.display.couch_port = "HDMI-A-1"
    cfg.cec.enabled = True
    cfg.cec.standby_targets = [0, 5]
    cfg.on_connect.power_on = ["cec:tv", "cec:receiver"]
    cfg.on_connect.run = "steam-bigpicture"
    cfg.screen_lock.enabled = True
    cfg.screen_lock.hold_inhibit = False

    save(cfg, path)
    loaded = load(path)

    assert loaded.configured is True
    assert loaded.display.desk_port == "HDMI-A-2"
    assert loaded.cec.standby_targets == [0, 5]
    assert loaded.on_connect.power_on == ["cec:tv", "cec:receiver"]
    assert loaded.on_connect.run == "steam-bigpicture"
    assert loaded.screen_lock.enabled is True
    assert loaded.screen_lock.hold_inhibit is False


def test_save_sets_restrictive_permissions(tmp_path):
    path = Path(tmp_path) / "config.toml"
    save(JoystickNotifyConfig(), path)
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600
