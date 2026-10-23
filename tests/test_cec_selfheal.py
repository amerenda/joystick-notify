import asyncio
from pathlib import Path

from joystick_notify.devices import cec as cec_discover
from joystick_notify.health import Health, Status


def _tracked(value):
    async def fake(adapter):
        return value() if callable(value) else value
    return fake


def test_ensure_adapter_returns_immediately_when_present(tmp_path, monkeypatch):
    monkeypatch.setattr(cec_discover, "discover_adapters", lambda: ["/dev/cec0"])
    monkeypatch.setattr(cec_discover, "_systemd_tracks", _tracked(True))

    async def no_watchdog(*args, **kwargs):
        raise AssertionError("watchdog must not run for a tracked adapter")

    monkeypatch.setattr(cec_discover, "_run_watchdog", no_watchdog)
    health = Health(path=Path(tmp_path) / "health.json")
    result = asyncio.run(cec_discover.ensure_adapter(health))
    assert result == "/dev/cec0"
    assert health.get("cec").status == Status.OK


def test_ensure_adapter_respects_cooldown(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(cec_discover, "discover_adapters", lambda: [])

    state_path = cec_discover._selfheal_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.touch()

    health = Health(path=Path(tmp_path) / "health.json")
    result = asyncio.run(cec_discover.ensure_adapter(health, cooldown_s=999))
    assert result is None
    assert health.get("cec").status == Status.FAILED
    assert "cooldown" in health.get("cec").reason


def test_ensure_adapter_self_heal_missing_binary_reports_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(cec_discover, "discover_adapters", lambda: [])
    health = Health(path=Path(tmp_path) / "health.json")

    async def fake_exec(*args, **kwargs):
        raise FileNotFoundError("sudo not found")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    result = asyncio.run(cec_discover.ensure_adapter(health, cooldown_s=0))
    assert result is None
    assert health.get("cec").status == Status.FAILED


def test_ensure_adapter_untracked_runs_watchdog_and_recovers(tmp_path, monkeypatch):
    # /dev/cec0 present but dev-cec0.device inactive (2026-10-23): the
    # watchdog must run and the adapter is reported reconfigured once
    # systemd tracks it again.
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(cec_discover, "discover_adapters", lambda: ["/dev/cec0"])
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)

        class Proc:
            async def wait(self):
                return 0

        return Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    checks = iter([False, True])
    monkeypatch.setattr(cec_discover, "_systemd_tracks", _tracked(lambda: next(checks)))

    health = Health(path=Path(tmp_path) / "health.json")
    result = asyncio.run(cec_discover.ensure_adapter(health, cooldown_s=0))
    assert result == "/dev/cec0"
    assert calls == [("sudo", "-n", "/usr/lib/joystick-notify/cec-watchdog.sh")]
    assert health.get("cec").status == Status.OK
    assert "reconfigured" in health.get("cec").reason


def test_ensure_adapter_untracked_on_cooldown_still_returns_adapter(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(cec_discover, "discover_adapters", lambda: ["/dev/cec0"])
    monkeypatch.setattr(cec_discover, "_systemd_tracks", _tracked(False))

    state_path = cec_discover._selfheal_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.touch()

    health = Health(path=Path(tmp_path) / "health.json")
    result = asyncio.run(cec_discover.ensure_adapter(health, cooldown_s=999))
    assert result == "/dev/cec0"
    assert health.get("cec").status == Status.FAILED
    assert "unconfigured" in health.get("cec").reason


def test_systemd_tracks_treats_missing_systemctl_as_tracked(monkeypatch):
    async def fake_exec(*args, **kwargs):
        raise FileNotFoundError("systemctl")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    assert asyncio.run(cec_discover._systemd_tracks("/dev/cec0")) is True
