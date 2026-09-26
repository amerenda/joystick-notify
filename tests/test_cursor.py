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


SUPPORT_INFO = (
    "DRM\n===\nAtomic Mode Setting on GPU 0: true\n\n"
    "Cursor\n======\nthemeName: {live}\nthemeSize: 30\n\n"
    "Options\n=======\nfocusPolicy: ClickToFocus\n"
)


class FakePlasma:
    """Models the real failure: plasma-apply-cursortheme compares against
    kcminputrc (config), not KWin's live theme, so it no-ops when the two
    have drifted apart. `reload_on_change` controls whether a real change
    reaches the live compositor."""

    def __init__(self, config, live, reload_on_change=True):
        self.config = config
        self.live = live
        self.reload_on_change = reload_on_change
        self.calls = []

    async def run(self, cmd, timeout=5.0):
        self.calls.append(cmd)
        if cmd[0] == "plasma-apply-cursortheme":
            if cmd[1] != self.config:
                self.config = cmd[1]
                if self.reload_on_change:
                    self.live = cmd[1]
            return 0, ""
        if cmd[:3] == ["qdbus6", "org.kde.KWin", "/KWin"] and cmd[3] == "supportInformation":
            return 0, SUPPORT_INFO.format(live=self.live)
        raise AssertionError(f"unexpected command {cmd}")

    def applied(self):
        return [c[1] for c in self.calls if c[0] == "plasma-apply-cursortheme"]


def _setup(tmp_path, monkeypatch, fake):
    monkeypatch.setattr(cursor, "_run", fake.run)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return Health(path=tmp_path / "health.json")


def test_activate_couch_applies_hide_theme(tmp_path, monkeypatch):
    fake = FakePlasma(config="breeze_cursors", live="breeze_cursors")
    health = _setup(tmp_path, monkeypatch, fake)
    cfg = CursorConfig(enabled=True, hide_theme="invisible", normal_theme="breeze_cursors")

    asyncio.run(cursor.activate_couch(cfg, health))

    assert fake.applied() == ["invisible"]
    assert fake.live == "invisible"
    assert (tmp_path / ".icons" / "default" / "index.theme").read_text() == "[Icon Theme]\nInherits=invisible\n"
    assert health.get("cursor").status == Status.OK


def test_activate_desk_restores_normal_theme(tmp_path, monkeypatch):
    fake = FakePlasma(config="invisible", live="invisible")
    health = _setup(tmp_path, monkeypatch, fake)
    cfg = CursorConfig(enabled=True, hide_theme="invisible", normal_theme="breeze_cursors")

    asyncio.run(cursor.activate_desk(cfg, health))

    assert fake.live == "breeze_cursors"
    assert (tmp_path / ".icons" / "default" / "index.theme").read_text() == "[Icon Theme]\nInherits=breeze_cursors\n"
    assert health.get("cursor").status == Status.OK


def test_activate_desk_forces_past_stale_kwin_state(tmp_path, monkeypatch):
    # The 2026-09-26 incident: kcminputrc already says breeze_cursors but
    # KWin's live theme is still "invisible". A plain apply is a no-op.
    fake = FakePlasma(config="breeze_cursors", live="invisible")
    health = _setup(tmp_path, monkeypatch, fake)
    cfg = CursorConfig(enabled=True, hide_theme="invisible", normal_theme="breeze_cursors")

    asyncio.run(cursor.activate_desk(cfg, health))

    assert fake.applied() == ["breeze_cursors", "invisible", "breeze_cursors"]
    assert fake.live == "breeze_cursors"
    assert health.get("cursor").status == Status.OK
    assert "forced" in health.get("cursor").reason


def test_activate_couch_forces_past_stale_kwin_state(tmp_path, monkeypatch):
    fake = FakePlasma(config="invisible", live="breeze_cursors")
    health = _setup(tmp_path, monkeypatch, fake)
    cfg = CursorConfig(enabled=True, hide_theme="invisible", normal_theme="breeze_cursors")

    asyncio.run(cursor.activate_couch(cfg, health))

    assert fake.applied() == ["invisible", "breeze_cursors", "invisible"]
    assert fake.live == "invisible"
    assert health.get("cursor").status == Status.OK


def test_force_falls_back_to_default_theme_when_no_other_theme_configured(tmp_path, monkeypatch):
    fake = FakePlasma(config="invisible", live="breeze_cursors")
    health = _setup(tmp_path, monkeypatch, fake)
    cfg = CursorConfig(enabled=True, hide_theme="invisible", normal_theme="")

    asyncio.run(cursor.activate_couch(cfg, health))

    assert fake.applied() == ["invisible", "default", "invisible"]
    assert fake.live == "invisible"


def test_reports_failed_when_live_theme_never_changes(tmp_path, monkeypatch):
    fake = FakePlasma(config="breeze_cursors", live="invisible", reload_on_change=False)
    health = _setup(tmp_path, monkeypatch, fake)
    cfg = CursorConfig(enabled=True, hide_theme="invisible", normal_theme="breeze_cursors")

    asyncio.run(cursor.activate_desk(cfg, health))

    assert health.get("cursor").status == Status.FAILED
    assert "invisible" in health.get("cursor").reason


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


def test_reports_failed_when_apply_command_fails(tmp_path, monkeypatch):
    async def fake_run(cmd, timeout=5.0):
        return 1, "boom"

    monkeypatch.setattr(cursor, "_run", fake_run)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    health = Health(path=tmp_path / "health.json")
    cfg = CursorConfig(enabled=True)

    asyncio.run(cursor.activate_couch(cfg, health))

    assert health.get("cursor").status == Status.FAILED
    assert "boom" in health.get("cursor").reason


def test_reports_failed_when_live_theme_unreadable(tmp_path, monkeypatch):
    async def fake_run(cmd, timeout=5.0):
        if cmd[0] == "plasma-apply-cursortheme":
            return 0, ""
        return 1, "no bus"

    monkeypatch.setattr(cursor, "_run", fake_run)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    health = Health(path=tmp_path / "health.json")
    cfg = CursorConfig(enabled=True)

    asyncio.run(cursor.activate_couch(cfg, health))

    assert health.get("cursor").status == Status.FAILED
    assert "could not read" in health.get("cursor").reason
