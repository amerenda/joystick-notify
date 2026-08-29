import asyncio
from pathlib import Path

from joystick_notify.actions import screen_lock
from joystick_notify.health import Health, Status

# Real loginctl output captured live 2026-08-21 on archlinux while
# diagnosing this exact issue -- SSH-spawned session scopes (SEAT="-")
# must not be mistaken for the real graphical seat session.
REAL_LOGINCTL_OUTPUT = """\
SESSION  UID USER SEAT  LEADER  CLASS   TTY  IDLE SINCE
      1 1000 alex -     1063    manager -    no   -
    158 1000 alex -     3084759 user    -    no   -
    171 1000 alex -     3105478 user    -    no   -
    172 1000 alex -     3113081 user    -    no   -
      2 1000 alex seat0 1101    user    tty1 no   -

5 sessions listed.
"""


def test_parse_seat_session_finds_the_real_seat_session():
    assert screen_lock.parse_seat_session(REAL_LOGINCTL_OUTPUT, "1000") == "2"


def test_parse_seat_session_ignores_seatless_sessions():
    # All the SEAT="-" rows come before the real one in the real output --
    # confirms this doesn't just grab the first matching-UID row.
    only_seatless = "\n".join(
        line for line in REAL_LOGINCTL_OUTPUT.splitlines() if "seat0" not in line
    )
    assert screen_lock.parse_seat_session(only_seatless, "1000") is None


def test_parse_seat_session_no_match_for_wrong_uid():
    assert screen_lock.parse_seat_session(REAL_LOGINCTL_OUTPUT, "9999") is None


def test_parse_seat_session_empty_output():
    assert screen_lock.parse_seat_session("", "1000") is None


def test_parse_dbus_bool_true():
    assert screen_lock.parse_dbus_bool("true") is True
    assert screen_lock.parse_dbus_bool("true\n") is True


def test_parse_dbus_bool_false():
    assert screen_lock.parse_dbus_bool("false") is False
    assert screen_lock.parse_dbus_bool("") is False


def test_unlock_reports_health_ok_when_verified_unlocked(tmp_path, monkeypatch):
    responses = iter(
        [
            (0, ""),  # kwriteconfig6 Autolock=false
            (0, ""),  # qdbus6 configure
            (0, ""),  # SetActive false
            (0, "SESSION  UID USER SEAT  LEADER  CLASS   TTY  IDLE SINCE\n      2 1000 alex seat0 1101 user tty1 no -\n"),  # loginctl (for _find_seat_session)
            (0, ""),  # loginctl unlock-session
            (0, "false"),  # GetActive -> unlocked, inside the verify loop
            (0, "false"),  # GetActive -> the post-loop still_locked check
            (0, ""),  # SimulateUserActivity
        ]
    )

    async def fake_run(cmd, timeout=5.0):
        return next(responses)

    monkeypatch.setattr(screen_lock, "_run", fake_run)
    monkeypatch.setattr("os.getuid", lambda: 1000)

    health = Health(path=Path(tmp_path) / "health.json")
    asyncio.run(screen_lock.unlock_and_disable_autolock(health, verify_attempts=1, verify_delay_s=0))

    assert health.get("screen_lock").status == Status.OK


def test_unlock_reports_health_failed_when_still_locked_after_all_attempts(tmp_path, monkeypatch):
    async def fake_run(cmd, timeout=5.0):
        if cmd[:1] == ["qdbus6"] and cmd[-1] == "org.freedesktop.ScreenSaver.GetActive":
            return 0, "true"  # never unlocks
        return 0, ""

    monkeypatch.setattr(screen_lock, "_run", fake_run)
    monkeypatch.setattr("os.getuid", lambda: 1000)

    health = Health(path=Path(tmp_path) / "health.json")
    asyncio.run(screen_lock.unlock_and_disable_autolock(health, verify_attempts=1, verify_delay_s=0))

    assert health.get("screen_lock").status == Status.FAILED


def test_activate_couch_noop_when_disabled(tmp_path):
    from joystick_notify.config.schema import ScreenLockConfig

    health = Health(path=Path(tmp_path) / "health.json")
    cfg = ScreenLockConfig(enabled=False)
    cookie = asyncio.run(screen_lock.activate_couch(cfg, health))
    assert cookie is None
    assert health.get("screen_lock").status == Status.OK
    assert "disabled" in health.get("screen_lock").reason


def test_activate_screensaver_reports_ok_when_verified_active(tmp_path, monkeypatch):
    responses = iter(
        [
            (0, ""),      # SetActive true
            (0, "true"),  # GetActive -> confirmed active
        ]
    )

    async def fake_run(cmd, timeout=5.0):
        return next(responses)

    monkeypatch.setattr(screen_lock, "_run", fake_run)
    health = Health(path=Path(tmp_path) / "health.json")

    result = asyncio.run(screen_lock.activate_screensaver(health))

    assert result is True
    assert health.get("screensaver").status == Status.OK


def test_activate_screensaver_reports_degraded_when_setactive_does_not_take(tmp_path, monkeypatch):
    responses = iter(
        [
            (0, ""),       # SetActive true
            (0, "false"),  # GetActive -> did not actually engage
        ]
    )

    async def fake_run(cmd, timeout=5.0):
        return next(responses)

    monkeypatch.setattr(screen_lock, "_run", fake_run)
    health = Health(path=Path(tmp_path) / "health.json")

    result = asyncio.run(screen_lock.activate_screensaver(health))

    assert result is False
    assert health.get("screensaver").status == Status.DEGRADED


def test_deactivate_screensaver_reports_ok(tmp_path, monkeypatch):
    calls = []

    async def fake_run(cmd, timeout=5.0):
        calls.append(cmd)
        return 0, ""

    monkeypatch.setattr(screen_lock, "_run", fake_run)
    health = Health(path=Path(tmp_path) / "health.json")

    asyncio.run(screen_lock.deactivate_screensaver(health))

    assert health.get("screensaver").status == Status.OK
    # SetActive(false) and SimulateUserActivity() both fired.
    assert any(cmd[-2:] == ["org.freedesktop.ScreenSaver.SetActive", "false"] for cmd in calls)
    assert any(cmd[-1] == "org.freedesktop.ScreenSaver.SimulateUserActivity" for cmd in calls)


def test_activate_desk_noop_when_disabled(tmp_path, monkeypatch):
    from joystick_notify.config.schema import ScreenLockConfig

    calls = []

    async def fake_run(cmd, timeout=5.0):
        calls.append(cmd)
        return 0, ""

    monkeypatch.setattr(screen_lock, "_run", fake_run)
    health = Health(path=Path(tmp_path) / "health.json")
    cfg = ScreenLockConfig(enabled=False)
    asyncio.run(screen_lock.activate_desk(cfg, health, cookie=None))
    assert calls == []  # nothing run at all when disabled
