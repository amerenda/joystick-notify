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
    return argparse.Namespace(idle=kw.get("idle"), hide_theme=kw.get("hide_theme"), normal_theme=kw.get("normal_theme"))


def test_hides_only_after_idle_threshold():
    t = mh.IdleTracker(15, now=100.0)
    assert not t.should_hide(114.9)
    assert t.should_hide(115.0)


def test_activity_resets_idle_timer():
    t = mh.IdleTracker(15, now=0.0)
    t.activity(10.0)
    assert not t.should_hide(24.9)
    assert t.should_hide(25.0)


def test_activity_while_visible_needs_no_restore():
    assert mh.IdleTracker(15, now=0.0).activity(1.0) is False


def test_activity_while_hidden_requests_restore():
    t = mh.IdleTracker(15, now=0.0)
    t.hidden = True
    assert t.activity(20.0) is True
    assert not t.should_hide(100.0)  # already hidden: never re-hide


def test_config_defaults_and_file_and_cli_override(tmp_path):
    missing = tmp_path / "nope.toml"
    cfg = mh.load_config(args(), missing)
    assert (cfg.idle_seconds, cfg.hide_theme, cfg.normal_theme) == (15.0, "invisible", "breeze_cursors")
    f = tmp_path / "c.toml"
    f.write_text('idle_seconds = 30\nnormal_theme = "Breeze_Light"\nbogus = 1\n')
    cfg = mh.load_config(args(), f)
    assert (cfg.idle_seconds, cfg.normal_theme) == (30.0, "Breeze_Light")
    assert mh.load_config(args(idle=5), f).idle_seconds == 5.0


def test_config_rejects_bad_values(tmp_path):
    with pytest.raises(SystemExit):
        mh.load_config(args(idle=0), tmp_path / "x.toml")
    with pytest.raises(SystemExit):
        mh.load_config(args(hide_theme="a", normal_theme="a"), tmp_path / "x.toml")


def test_set_theme_forces_via_nudge_when_live_state_is_stale(monkeypatch):
    live = ["invisible", "invisible", "breeze_cursors"]  # before, after plain apply, after nudge+apply
    calls = []
    monkeypatch.setattr(mh, "live_theme", lambda: live.pop(0))
    monkeypatch.setattr(mh, "_run", lambda cmd: calls.append(cmd) or (0, ""))
    assert mh.set_theme("breeze_cursors", "invisible") is True
    assert [c[-1] for c in calls] == ["breeze_cursors", "invisible", "breeze_cursors"]


def test_set_theme_noop_when_already_live(monkeypatch):
    monkeypatch.setattr(mh, "live_theme", lambda: "breeze_cursors")
    monkeypatch.setattr(mh, "_run", lambda cmd: pytest.fail("should not run"))
    assert mh.set_theme("breeze_cursors", "invisible") is True


def test_session_env_fills_missing_wayland_display(tmp_path):
    (tmp_path / "wayland-0").touch()
    (tmp_path / "wayland-0.lock").touch()
    env = mh.session_env({"XDG_RUNTIME_DIR": str(tmp_path)})
    assert env["WAYLAND_DISPLAY"] == "wayland-0"
    assert env["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path={tmp_path}/bus"


def test_session_env_keeps_existing_values(tmp_path):
    env = mh.session_env({"XDG_RUNTIME_DIR": str(tmp_path), "WAYLAND_DISPLAY": "wayland-9"})
    assert env["WAYLAND_DISPLAY"] == "wayland-9"
