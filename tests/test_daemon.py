from pathlib import Path

from joystick_notify.config.schema import JoystickNotifyConfig
from joystick_notify.daemon import check_startup_health, run_doctor
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
