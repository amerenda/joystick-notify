import asyncio
from pathlib import Path

from joystick_notify.actions import notifications
from joystick_notify.config.schema import NotificationsConfig
from joystick_notify.health import Health, Status


def test_parse_inhibit_cookie_extracts_uint():
    assert notifications.parse_inhibit_cookie("u 1") == "1"
    assert notifications.parse_inhibit_cookie("u 42\n") == "42"


def test_parse_inhibit_cookie_none_on_unexpected_output():
    assert notifications.parse_inhibit_cookie("") is None
    assert notifications.parse_inhibit_cookie("Failed to activate service") is None
    assert notifications.parse_inhibit_cookie("s hello") is None


def test_acquire_inhibit_returns_cookie_on_success(monkeypatch):
    calls = []

    async def fake_run(cmd, timeout=5.0):
        calls.append(cmd)
        return 0, "u 1\n"

    monkeypatch.setattr(notifications, "_run", fake_run)

    cookie = asyncio.run(notifications.acquire_inhibit())

    assert cookie == "1"
    assert calls[0][:4] == ["busctl", "--user", "call", "org.freedesktop.Notifications"]
    assert "Inhibit" in calls[0]


def test_acquire_inhibit_returns_none_and_logs_on_failure(monkeypatch, caplog):
    async def fake_run(cmd, timeout=5.0):
        return 1, "Failed to activate service 'org.freedesktop.Notifications'"

    monkeypatch.setattr(notifications, "_run", fake_run)

    with caplog.at_level("WARNING", logger="joystick_notify.actions.notifications"):
        cookie = asyncio.run(notifications.acquire_inhibit())

    assert cookie is None
    assert any("failed to acquire" in r.message for r in caplog.records)


def test_release_inhibit_calls_uninhibit_with_cookie(monkeypatch):
    calls = []

    async def fake_run(cmd, timeout=5.0):
        calls.append(cmd)
        return 0, ""

    monkeypatch.setattr(notifications, "_run", fake_run)

    asyncio.run(notifications.release_inhibit("1"))

    assert calls[0][:4] == ["busctl", "--user", "call", "org.freedesktop.Notifications"]
    assert calls[0][-2:] == ["u", "1"]


def test_activate_couch_noop_when_disabled(tmp_path):
    health = Health(path=Path(tmp_path) / "health.json")
    cfg = NotificationsConfig(enabled=False)

    cookie = asyncio.run(notifications.activate_couch(cfg, health))

    assert cookie is None
    assert health.get("notifications").status == Status.OK
    assert "disabled" in health.get("notifications").reason


def test_activate_couch_reports_ok_and_returns_cookie_when_acquired(tmp_path, monkeypatch):
    async def fake_run(cmd, timeout=5.0):
        return 0, "u 7\n"

    monkeypatch.setattr(notifications, "_run", fake_run)
    health = Health(path=Path(tmp_path) / "health.json")
    cfg = NotificationsConfig(enabled=True)

    cookie = asyncio.run(notifications.activate_couch(cfg, health))

    assert cookie == "7"
    assert health.get("notifications").status == Status.OK
    assert "suppressed" in health.get("notifications").reason


def test_activate_couch_reports_degraded_when_inhibit_fails(tmp_path, monkeypatch):
    async def fake_run(cmd, timeout=5.0):
        return 1, "error"

    monkeypatch.setattr(notifications, "_run", fake_run)
    health = Health(path=Path(tmp_path) / "health.json")
    cfg = NotificationsConfig(enabled=True)

    cookie = asyncio.run(notifications.activate_couch(cfg, health))

    assert cookie is None
    assert health.get("notifications").status == Status.DEGRADED


def test_activate_desk_noop_when_disabled(tmp_path, monkeypatch):
    calls = []

    async def fake_run(cmd, timeout=5.0):
        calls.append(cmd)
        return 0, ""

    monkeypatch.setattr(notifications, "_run", fake_run)
    health = Health(path=Path(tmp_path) / "health.json")
    cfg = NotificationsConfig(enabled=False)

    asyncio.run(notifications.activate_desk(cfg, health, cookie="1"))

    assert calls == []  # nothing run at all when disabled


def test_activate_desk_releases_cookie_and_reports_ok(tmp_path, monkeypatch):
    calls = []

    async def fake_run(cmd, timeout=5.0):
        calls.append(cmd)
        return 0, ""

    monkeypatch.setattr(notifications, "_run", fake_run)
    health = Health(path=Path(tmp_path) / "health.json")
    cfg = NotificationsConfig(enabled=True)

    asyncio.run(notifications.activate_desk(cfg, health, cookie="7"))

    assert len(calls) == 1
    assert calls[0][-2:] == ["u", "7"]
    assert health.get("notifications").status == Status.OK
    assert "released" in health.get("notifications").reason


def test_activate_desk_skips_release_when_no_cookie_held(tmp_path, monkeypatch):
    calls = []

    async def fake_run(cmd, timeout=5.0):
        calls.append(cmd)
        return 0, ""

    monkeypatch.setattr(notifications, "_run", fake_run)
    health = Health(path=Path(tmp_path) / "health.json")
    cfg = NotificationsConfig(enabled=True)

    asyncio.run(notifications.activate_desk(cfg, health, cookie=None))

    assert calls == []
    assert health.get("notifications").status == Status.OK
