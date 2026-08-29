import asyncio
from pathlib import Path

from joystick_notify.actions import cursor
from joystick_notify.config.schema import CursorConfig
from joystick_notify.health import Health, Status


def test_icons_default_theme_content():
    assert cursor.icons_default_theme_content("invisible") == "[Icon Theme]\nInherits=invisible\n"


def test_activate_couch_noop_when_disabled(tmp_path, monkeypatch):
    calls = []

    async def fake_run(cmd, timeout=5.0):
        calls.append(cmd)
        return 0, ""

    monkeypatch.setattr(cursor, "_run", fake_run)
    health = Health(path=Path(tmp_path) / "health.json")
    cfg = CursorConfig(enabled=False)

    asyncio.run(cursor.activate_couch(cfg, health))

    assert calls == []
    assert health.get("cursor") is None


def test_activate_desk_noop_when_disabled(tmp_path, monkeypatch):
    calls = []

    async def fake_run(cmd, timeout=5.0):
        calls.append(cmd)
        return 0, ""

    monkeypatch.setattr(cursor, "_run", fake_run)
    health = Health(path=Path(tmp_path) / "health.json")
    cfg = CursorConfig(enabled=False)

    asyncio.run(cursor.activate_desk(cfg, health))

    assert calls == []


def test_activate_couch_applies_hide_theme(tmp_path, monkeypatch):
    calls = []

    async def fake_run(cmd, timeout=5.0):
        calls.append(cmd)
        return 0, ""

    monkeypatch.setattr(cursor, "_run", fake_run)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    health = Health(path=tmp_path / "health.json")
    cfg = CursorConfig(enabled=True, hide_theme="invisible")

    asyncio.run(cursor.activate_couch(cfg, health))

    assert calls[0] == ["kwriteconfig6", "--file", "kcminputrc", "--group", "Mouse", "--key", "cursorTheme", "invisible"]
    assert calls[1] == ["qdbus6", "org.kde.KWin", "/KWin", "reconfigure"]
    assert (tmp_path / ".icons" / "default" / "index.theme").read_text() == "[Icon Theme]\nInherits=invisible\n"
    assert health.get("cursor").status == Status.OK


def test_activate_desk_restores_normal_theme(tmp_path, monkeypatch):
    calls = []

    async def fake_run(cmd, timeout=5.0):
        calls.append(cmd)
        return 0, ""

    monkeypatch.setattr(cursor, "_run", fake_run)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    health = Health(path=tmp_path / "health.json")
    cfg = CursorConfig(enabled=True, hide_theme="invisible", normal_theme="breeze_cursors")

    asyncio.run(cursor.activate_desk(cfg, health))

    assert calls[0][-1] == "breeze_cursors"
    assert (tmp_path / ".icons" / "default" / "index.theme").read_text() == "[Icon Theme]\nInherits=breeze_cursors\n"


def test_activate_desk_leaves_theme_alone_when_normal_theme_unset(tmp_path, monkeypatch):
    calls = []

    async def fake_run(cmd, timeout=5.0):
        calls.append(cmd)
        return 0, ""

    monkeypatch.setattr(cursor, "_run", fake_run)
    health = Health(path=Path(tmp_path) / "health.json")
    cfg = CursorConfig(enabled=True, normal_theme="")

    asyncio.run(cursor.activate_desk(cfg, health))

    assert calls == []
    assert health.get("cursor").status == Status.OK
    assert "leaving cursor theme as-is" in health.get("cursor").reason


def test_activate_couch_reports_failed_when_kwriteconfig_fails(tmp_path, monkeypatch):
    async def fake_run(cmd, timeout=5.0):
        return 1, "boom"

    monkeypatch.setattr(cursor, "_run", fake_run)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    health = Health(path=tmp_path / "health.json")
    cfg = CursorConfig(enabled=True)

    asyncio.run(cursor.activate_couch(cfg, health))

    assert health.get("cursor").status == Status.FAILED
    assert "boom" in health.get("cursor").reason
    # Never got to writing ~/.icons/default or calling KWin reconfigure.
    assert not (tmp_path / ".icons").exists()


def test_activate_couch_reports_failed_when_kwin_reconfigure_fails(tmp_path, monkeypatch):
    responses = iter(
        [
            (0, ""),        # kwriteconfig6
            (1, "no bus"),  # qdbus6 reconfigure
        ]
    )

    async def fake_run(cmd, timeout=5.0):
        return next(responses)

    monkeypatch.setattr(cursor, "_run", fake_run)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    health = Health(path=tmp_path / "health.json")
    cfg = CursorConfig(enabled=True)

    asyncio.run(cursor.activate_couch(cfg, health))

    assert health.get("cursor").status == Status.FAILED
    assert "no bus" in health.get("cursor").reason
