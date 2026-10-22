import argparse
import importlib.machinery
import importlib.util
from pathlib import Path

import pytest

_path = Path(__file__).parent.parent / "tools" / "mouse-hider" / "mouse-hider"
_loader = importlib.machinery.SourceFileLoader("mouse_hider", str(_path))
_spec = importlib.util.spec_from_loader("mouse_hider", _loader)
mh = importlib.util.module_from_spec(_spec)
import sys
sys.modules["mouse_hider"] = mh
_loader.exec_module(mh)


def args(**kw):
    return argparse.Namespace(idle=kw.get("idle"))


class FakeKWin:
    """Answers the qdbus6/kwriteconfig6/plasma-apply-cursortheme calls."""

    def __init__(self, loaded=False, theme="breeze_cursors", load_works=True):
        self.loaded, self.theme, self.load_works = loaded, theme, load_works
        self.calls = []

    def __call__(self, cmd):
        self.calls.append(cmd)
        method = cmd[3].rsplit(".", 1)[-1] if cmd[0] == "qdbus6" and len(cmd) > 3 else None
        if method == "isEffectLoaded":
            return 0, "true" if self.loaded else "false"
        if method == "loadEffect":
            self.loaded = self.load_works
        elif method == "unloadEffect":
            self.loaded = False
        elif method == "supportInformation":
            return 0, f"Platform\nfoo\nCursor\n======\nthemeName: {self.theme}\n"
        elif cmd[0] == "plasma-apply-cursortheme":
            self.theme = cmd[1]
        return 0, ""


def test_config_defaults_and_file_and_cli_override(tmp_path):
    cfg = mh.load_config(args(), tmp_path / "nope.toml")
    assert (cfg.idle_seconds, cfg.normal_theme) == (15.0, "breeze_cursors")
    f = tmp_path / "c.toml"
    f.write_text('idle_seconds = 30\nnormal_theme = "Breeze_Light"\nhide_theme = "old"\nbogus = 1\n')
    cfg = mh.load_config(args(), f)
    assert (cfg.idle_seconds, cfg.normal_theme) == (30.0, "Breeze_Light")
    assert mh.load_config(args(idle=5), f).idle_seconds == 5.0


def test_config_rejects_bad_values(tmp_path):
    with pytest.raises(SystemExit):
        mh.load_config(args(idle=0), tmp_path / "x.toml")


def test_inactivity_duration_is_whole_seconds_and_never_zero():
    assert mh.Config(idle_seconds=15).inactivity_duration == 15
    assert mh.Config(idle_seconds=0.2).inactivity_duration == 1  # 0 would mean never hide


def test_enable_hiding_configures_then_loads_and_verifies(monkeypatch):
    kwin = FakeKWin()
    monkeypatch.setattr(mh, "_run", kwin)
    assert mh.enable_hiding(mh.Config(idle_seconds=10)) is True
    assert kwin.loaded
    writes = [c for c in kwin.calls if c[0] == "kwriteconfig6"]
    assert ["--key", "InactivityDuration", "10"] == writes[0][-3:]
    assert ["--key", "HideOnTyping", "false"] == writes[1][-3:]
    assert all("--group" in c and "Effect-hidecursor" in c for c in writes)


def test_enable_hiding_reports_failure_when_kwin_refuses(monkeypatch):
    monkeypatch.setattr(mh, "_run", FakeKWin(load_works=False))
    assert mh.enable_hiding(mh.Config()) is False


def test_enable_hiding_reconfigures_an_already_loaded_effect(monkeypatch):
    kwin = FakeKWin(loaded=True)
    monkeypatch.setattr(mh, "_run", kwin)
    assert mh.enable_hiding(mh.Config()) is True
    methods = [c[3] for c in kwin.calls if c[0] == "qdbus6"]
    assert "org.kde.kwin.Effects.reconfigureEffect" in methods
    assert "org.kde.kwin.Effects.loadEffect" not in methods


def test_make_visible_unloads_effect_and_cures_leftover_hidden_theme(monkeypatch):
    kwin = FakeKWin(loaded=True, theme="invisible")
    monkeypatch.setattr(mh, "_run", kwin)
    assert mh.make_visible(mh.Config()) is True
    assert not kwin.loaded
    assert kwin.theme == "breeze_cursors"


def test_make_visible_leaves_a_normal_theme_alone(monkeypatch):
    kwin = FakeKWin(theme="Breeze_Light")
    monkeypatch.setattr(mh, "_run", kwin)
    assert mh.make_visible(mh.Config()) is True
    assert not any(c[0] == "plasma-apply-cursortheme" for c in kwin.calls)


def test_session_env_fills_missing_wayland_display(tmp_path):
    (tmp_path / "wayland-0").touch()
    (tmp_path / "wayland-0.lock").touch()
    env = mh.session_env({"XDG_RUNTIME_DIR": str(tmp_path)})
    assert env["WAYLAND_DISPLAY"] == "wayland-0"
    assert env["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path={tmp_path}/bus"


def test_session_env_keeps_existing_values(tmp_path):
    env = mh.session_env({"XDG_RUNTIME_DIR": str(tmp_path), "WAYLAND_DISPLAY": "wayland-9"})
    assert env["WAYLAND_DISPLAY"] == "wayland-9"
