import asyncio
from pathlib import Path

from joystick_notify.devices import cec as cec_discover
from joystick_notify.health import Health, Status


def test_ensure_adapter_returns_immediately_when_present(tmp_path, monkeypatch):
    monkeypatch.setattr(cec_discover, "discover_adapters", lambda: ["/dev/cec0"])
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
