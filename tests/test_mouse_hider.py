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


class FakeKWin:
    """Answers the qdbus6/kwriteconfig6/plasma-apply-cursortheme calls."""

    def __init__(self, loaded=False, theme="breeze_cursors", load_works=True, forced_available=False):
        self.loaded, self.theme, self.load_works = loaded, theme, load_works
        self.forced_available, self.forced_loaded = forced_available, False
        self.calls = []

    def __call__(self, cmd):
        self.calls.append(cmd)
        method = cmd[3].rsplit(".", 1)[-1] if cmd[0] == "qdbus6" and len(cmd) > 3 else None
        forced = len(cmd) > 4 and cmd[4] == mh.FORCE_EFFECT
        if method == "isEffectLoaded":
            return 0, "true" if (self.forced_loaded if forced else self.loaded) else "false"
        if method == "loadEffect":
            if forced:
                self.forced_loaded = self.forced_available
            else:
                self.loaded = self.load_works
        elif method == "unloadEffect":
            if forced:
                self.forced_loaded = False
            else:
                self.loaded = False
        elif method == "supportInformation":
            return 0, f"Platform\nfoo\nCursor\n======\nthemeName: {self.theme}\n"
        elif cmd[0] == "plasma-apply-cursortheme":
            self.theme = cmd[1]
        return 0, ""


def test_config_defaults_and_reads_joystick_notify_config(tmp_path):
    assert mh.load_config(tmp_path / "nope.toml").idle_seconds == 15.0
    f = tmp_path / "config.toml"
    f.write_text("[mouse_hider]\nidle_seconds = 30\n[idle]\nidle_after_s = 5\n")
    assert mh.load_config(f).idle_seconds == 30.0
    f.write_text("[idle]\nidle_after_s = 5\n")
    assert mh.load_config(f).idle_seconds == 15.0


def test_config_rejects_bad_values(tmp_path):
    f = tmp_path / "config.toml"
    f.write_text("[mouse_hider]\nidle_seconds = 0\n")
    with pytest.raises(SystemExit):
        mh.load_config(f)


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
    methods = [c[3].rsplit(".", 1)[-1] for c in kwin.calls if c[0] == "qdbus6" and len(c) > 3]
    # loadEffect alone keeps KWin's cached kwinrc; reconfigureEffect reparses it.
    assert methods.index("loadEffect") < methods.index("reconfigureEffect")


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


@pytest.mark.parametrize(
    "ev_type,code,expected",
    [
        (mh.EV_REL, 0, True),  # REL_X
        (mh.EV_ABS, 0, True),  # absolute pointer (Sunshine's virtual mouse)
        (mh.EV_KEY, 0x110, True),  # BTN_LEFT
        (mh.EV_KEY, 0x112, True),  # BTN_MIDDLE
        (mh.EV_KEY, 28, False),  # KEY_ENTER: a CEC remote or keyboard is not pointer activity
        (mh.EV_KEY, 0x130, False),  # BTN_SOUTH: gamepad button
        (0x00, 0, False),  # EV_SYN
    ],
)
def test_is_pointer_activity(ev_type, code, expected):
    assert mh.is_pointer_activity(ev_type, code) is expected


def test_run_loop_hides_now_then_switches_to_idle_seconds_after_motion(monkeypatch):
    import threading

    kwin = FakeKWin()
    monkeypatch.setattr(mh, "_run", kwin)
    stop = threading.Event()
    activity = iter([True])

    def fake_wait(timeout):
        if next(activity, None):
            return True
        stop.set()
        return False

    monkeypatch.setattr(mh, "wait_for_pointer_activity", fake_wait)
    monkeypatch.setattr(stop, "wait", lambda timeout: stop.set() or True)
    monkeypatch.setattr(mh, "SETTLE_S", 0)
    mh.run_loop(mh.Config(always_hidden=False, idle_seconds=15, hold_seconds=0), stop)

    durations = [c[-1] for c in kwin.calls if c[0] == "kwriteconfig6" and "InactivityDuration" in c]
    methods = [c[3].rsplit(".", 1)[-1] for c in kwin.calls if c[0] == "qdbus6" and c[2] == "/Effects"]
    assert durations == ["1", "15"]  # hide now, then the real timeout
    assert [m for m in methods if m != "isEffectLoaded"] == ["loadEffect", "reconfigureEffect", "reconfigureEffect", "unloadEffect"]
    assert not kwin.loaded  # stopped: effect unloaded


def test_run_loop_keeps_hide_now_timeout_during_hold_and_signals_ready(monkeypatch):
    import threading

    kwin = FakeKWin()
    monkeypatch.setattr(mh, "_run", kwin)
    monkeypatch.setattr(mh, "SETTLE_S", 0)
    ready = []
    monkeypatch.setattr(mh, "notify_ready", lambda: ready.append(len([c for c in kwin.calls if any(x.endswith(".loadEffect") for x in c)])))
    stop = threading.Event()
    activity = iter([True, True])

    def fake_wait(timeout):
        if next(activity, None):
            return True
        stop.set()
        return False

    monkeypatch.setattr(mh, "wait_for_pointer_activity", fake_wait)
    monkeypatch.setattr(stop, "wait", lambda timeout: False)
    mh.run_loop(mh.Config(always_hidden=False, idle_seconds=15, hold_seconds=3600), stop)

    durations = [c[-1] for c in kwin.calls if c[0] == "kwriteconfig6" and "InactivityDuration" in c]
    assert durations == ["1"]  # motion inside the hold never relaxes the timeout
    assert ready == [1]  # READY only after the effect was loaded


def test_env_flag_defaults_true_and_reads_off_values():
    assert mh.env_flag("X", True, {}) is True
    assert mh.env_flag("X", True, {"X": ""}) is True
    for off in ("0", "false", "No", "OFF"):
        assert mh.env_flag("X", True, {"X": off}) is False
    assert mh.env_flag("X", False, {"X": "1"}) is True


def test_load_config_always_hidden_follows_the_env_var(tmp_path, monkeypatch):
    monkeypatch.delenv("MOUSE_HIDER_ALWAYS_HIDDEN", raising=False)
    assert mh.load_config(tmp_path / "nope.toml").always_hidden is True
    monkeypatch.setenv("MOUSE_HIDER_ALWAYS_HIDDEN", "0")
    assert mh.load_config(tmp_path / "nope.toml").always_hidden is False


@pytest.mark.parametrize(
    "name, keys, rel_x, expected",
    [
        ("Logitech G309", [0x110, 30], True, True),
        ("keyd virtual pointer", [0x110], True, True),
        ("Valve Software Steam Controller Puck", [30, 0x110], True, True),
        ("libvirtualhid Mouse", [0x110], True, False),  # Sunshine remote input stays usable
        ("libvirtualhid Mouse (Absolute)", [0x110], False, False),
        ("Xbox Controller", [0x110, 0x130, 0x131], True, False),  # gamepad: never grab
        ("Some Keyboard", [30, 31], False, False),  # not a pointer
    ],
)
def test_should_grab(name, keys, rel_x, expected):
    assert mh.should_grab(name, keys, rel_x) is expected


def _always_hidden_run(monkeypatch, kwin):
    import threading

    monkeypatch.setattr(mh, "_run", kwin)
    events = []

    class FakeGrabber:
        def scan(self):
            events.append("scan")
            return 2

        def release(self):
            events.append("release")

    monkeypatch.setattr(mh, "PointerGrabber", FakeGrabber)
    monkeypatch.setattr(mh, "SETTLE_S", 0)
    stop = threading.Event()
    waits = iter([False, True])  # one full tick, then stop
    monkeypatch.setattr(stop, "wait", lambda timeout: next(waits))
    mh.run_loop(mh.Config(always_hidden=True, idle_seconds=15), stop)
    return events


def test_run_loop_always_hidden_loads_jn_hidecursor_grabs_and_releases(monkeypatch):
    kwin = FakeKWin(forced_available=True)
    events = _always_hidden_run(monkeypatch, kwin)

    loaded = [c[4] for c in kwin.calls if c[0] == "qdbus6" and c[3] == "org.kde.kwin.Effects.loadEffect"]
    assert loaded == [mh.FORCE_EFFECT]  # the timer-based stock effect is never needed
    assert not any(c[0] == "kwriteconfig6" for c in kwin.calls)
    assert events == ["scan", "scan", "release"]  # at start, hotplug rescan, then released on stop
    assert not kwin.forced_loaded and not kwin.loaded  # stopped: pointer back


def test_run_loop_always_hidden_falls_back_to_stock_effect_when_plugin_missing(monkeypatch):
    kwin = FakeKWin(forced_available=False)
    events = _always_hidden_run(monkeypatch, kwin)

    durations = [c[-1] for c in kwin.calls if c[0] == "kwriteconfig6" and "InactivityDuration" in c]
    assert durations == ["1"]  # never relaxed to idle_seconds
    assert events == ["scan", "scan", "release"]
    assert not kwin.loaded


def test_notify_ready_is_a_noop_without_notify_socket(monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    mh.notify_ready()
