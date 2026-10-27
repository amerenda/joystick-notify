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
    return argparse.Namespace(idle=kw.get("idle"), seconds=kw.get("seconds"))


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
    activity = iter(["/dev/input/event9 'mouse' EV_REL REL_X"])

    def fake_wait(timeout, cfg=None):
        if (source := next(activity, None)):
            return source
        stop.set()
        return None

    monkeypatch.setattr(mh, "wait_for_pointer_activity", fake_wait)
    monkeypatch.setattr(stop, "wait", lambda timeout: stop.set() or True)
    monkeypatch.setattr(mh, "open_devices", lambda: [])
    mh.run_loop(mh.Config(idle_seconds=15), stop)

    durations = [c[-1] for c in kwin.calls if c[0] == "kwriteconfig6" and "InactivityDuration" in c]
    methods = [c[3].rsplit(".", 1)[-1] for c in kwin.calls if c[0] == "qdbus6" and c[2] == "/Effects"]
    assert durations == ["1", "15"]  # hide now, then the real timeout
    assert methods == ["isEffectLoaded", "loadEffect", "reconfigureEffect", "isEffectLoaded",
                       "reconfigureEffect", "isEffectLoaded", "unloadEffect", "isEffectLoaded"]
    assert not kwin.loaded  # stopped: effect unloaded


# --- Controller input must not reveal the pointer ---
# Pressing d-pad-up on the Steam Controller brought the pointer back: the
# Puck exposes keyboard/mouse evdev nodes (lizard mode) and KWin shows the
# pointer on pointer events from any device.

from types import SimpleNamespace

VALVE, EIGHTBITDO, LOGITECH = 0x28DE, 0x2DC8, 0x046D
LIZARD_CAPS = {mh.EV_KEY: [28, 103, 0x110], mh.EV_REL: [0, 1, 8]}  # keys + mouse
GAMEPAD_CAPS = {mh.EV_KEY: [0x130, 0x131], mh.EV_ABS: [0, 1]}
MOUSE_CAPS = {mh.EV_KEY: [0x110, 0x111], mh.EV_REL: [0, 1]}
VENDORS = (VALVE, EIGHTBITDO)


@pytest.mark.parametrize(
    "vendor,caps,expected",
    [
        (VALVE, LIZARD_CAPS, True),  # Puck keyboard/mouse node
        (EIGHTBITDO, LIZARD_CAPS, True),
        (VALVE, GAMEPAD_CAPS, False),  # the node a game or Steam reads stays untouched
        (LOGITECH, MOUSE_CAPS, False),  # a real mouse must keep revealing the pointer
        (VALVE, {mh.EV_KEY: [28, 103]}, True),  # keyboard-only node (d-pad as arrow keys)
        (VALVE, {}, False),
    ],
)
def test_is_controller_input_node(vendor, caps, expected):
    assert mh.is_controller_input_node(vendor, caps, VENDORS) is expected


class FakeEvdev:
    def __init__(self, path, name, vendor, caps, grab_error=None):
        self.path, self.name, self.info = path, name, SimpleNamespace(vendor=vendor)
        self._caps, self.grab_error = caps, grab_error
        self.grabbed = self.closed = False

    def capabilities(self):
        return self._caps

    def grab(self):
        if self.grab_error:
            raise self.grab_error
        self.grabbed = True

    def ungrab(self):
        self.grabbed = False

    def close(self):
        self.closed = True


def make_grabs(devices, **cfg):
    current = {"devices": devices}
    config = mh.Config(controller_vendors=VENDORS, **{"grab_controllers": True, **cfg})
    return mh.ControllerGrabs(config, opener=lambda: list(current["devices"])), current


def test_grabs_only_controller_keyboard_mouse_nodes():
    puck = FakeEvdev("/dev/input/event3", "Valve Puck", VALVE, LIZARD_CAPS)
    pad = FakeEvdev("/dev/input/event8", "Xbox pad", VALVE, GAMEPAD_CAPS)
    mouse = FakeEvdev("/dev/input/event9", "Logitech", LOGITECH, MOUSE_CAPS)
    grabs, _ = make_grabs([puck, pad, mouse])

    notes = grabs.sync()

    assert puck.grabbed and not pad.grabbed and not mouse.grabbed
    assert list(grabs.grabbed) == ["/dev/input/event3"]
    assert len(notes) == 1 and "event3" in notes[0]
    assert pad.closed and mouse.closed and not puck.closed


def test_grabs_do_nothing_when_disabled():
    puck = FakeEvdev("/dev/input/event3", "Valve Puck", VALVE, LIZARD_CAPS)
    grabs, _ = make_grabs([puck], grab_controllers=False)
    assert grabs.sync() == [] and not puck.grabbed


def test_grabs_are_not_repeated_on_every_sync():
    puck = FakeEvdev("/dev/input/event3", "Valve Puck", VALVE, LIZARD_CAPS)
    grabs, current = make_grabs([puck])
    grabs.sync()
    again = FakeEvdev("/dev/input/event3", "Valve Puck", VALVE, LIZARD_CAPS)
    current["devices"] = [again]
    assert grabs.sync() == []
    assert again.closed and not again.grabbed and puck.grabbed


def test_grabs_pick_up_a_node_that_appears_later_and_drop_one_that_vanishes():
    first = FakeEvdev("/dev/input/event3", "Valve Puck", VALVE, LIZARD_CAPS)
    grabs, current = make_grabs([first])
    grabs.sync()
    second = FakeEvdev("/dev/input/event4", "8BitDo", EIGHTBITDO, LIZARD_CAPS)
    current["devices"] = [second]
    notes = grabs.sync()
    assert second.grabbed and not first.grabbed
    assert list(grabs.grabbed) == ["/dev/input/event4"]
    assert any("event3 is gone" in n for n in notes)


def test_grab_failure_is_reported_and_not_fatal():
    puck = FakeEvdev("/dev/input/event3", "Valve Puck", VALVE, LIZARD_CAPS, grab_error=OSError("busy"))
    grabs, _ = make_grabs([puck])
    notes = grabs.sync()
    assert grabs.grabbed == {} and "could not grab" in notes[0] and puck.closed


def test_release_all_ungrabs_everything():
    puck = FakeEvdev("/dev/input/event3", "Valve Puck", VALVE, LIZARD_CAPS)
    grabs, _ = make_grabs([puck])
    grabs.sync()
    grabs.release_all()
    assert not puck.grabbed and puck.closed and grabs.grabbed == {}


def test_pointer_devices_leave_out_grabbed_controllers(monkeypatch):
    puck = FakeEvdev("/dev/input/event3", "Valve Puck", VALVE, LIZARD_CAPS)
    mouse = FakeEvdev("/dev/input/event9", "Logitech", LOGITECH, MOUSE_CAPS)
    monkeypatch.setattr(mh, "open_devices", lambda: [puck, mouse])
    cfg = mh.Config(controller_vendors=VENDORS, grab_controllers=True)
    assert mh.pointer_devices(cfg) == [mouse]
    monkeypatch.setattr(mh, "open_devices", lambda: [puck, mouse])
    assert mh.pointer_devices(mh.Config(grab_controllers=False, controller_vendors=VENDORS)) == [puck, mouse]


def test_config_reads_controller_settings(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('grab_controllers = false\ncontroller_vendors = ["28de", "054c"]\n')
    cfg = mh.load_config(args(), path)
    assert cfg.grab_controllers is False and cfg.controller_vendors == (0x28DE, 0x054C)


def test_config_grabbing_is_opt_in(tmp_path):
    cfg = mh.load_config(args(), tmp_path / "missing.toml")
    assert cfg.grab_controllers is False and cfg.controller_vendors == (0x28DE, 0x2DC8)


def test_run_loop_grabs_nothing_by_default(monkeypatch):
    import threading

    puck = FakeEvdev("/dev/input/event3", "Valve Puck", VALVE, LIZARD_CAPS)
    monkeypatch.setattr(mh, "_run", FakeKWin())
    monkeypatch.setattr(mh, "open_devices", lambda: [puck])
    stop = threading.Event()
    monkeypatch.setattr(mh, "wait_for_pointer_activity", lambda timeout, cfg=None: stop.set())
    mh.run_loop(mh.Config(), stop)
    assert not puck.grabbed


def test_run_loop_releases_grabs_when_it_stops(monkeypatch):
    import threading

    puck = FakeEvdev("/dev/input/event3", "Valve Puck", VALVE, LIZARD_CAPS)
    monkeypatch.setattr(mh, "_run", FakeKWin())
    monkeypatch.setattr(mh, "open_devices", lambda: [puck])
    stop = threading.Event()
    monkeypatch.setattr(mh, "wait_for_pointer_activity", lambda timeout, cfg=None: stop.set())
    mh.run_loop(mh.Config(controller_vendors=VENDORS, grab_controllers=True), stop)
    assert not puck.grabbed and puck.closed
