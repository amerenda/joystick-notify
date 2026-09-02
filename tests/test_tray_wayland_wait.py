"""Regression coverage for tray.py's main() bailing out before touching
Qt at all when no live Wayland socket is found -- see
session_env.wait_for_wayland_socket's docstring for the incident this
closes. PyQt6 isn't installed in this test environment, so a passing run
here is itself proof main() never reaches the Qt import on the timeout
path.

main() does `from ..session_env import ...` as a local import, so the
patch target is session_env's own module attributes (resolved at call
time), not anything on the tray module itself.
"""
from joystick_notify import session_env
from joystick_notify.tray import tray


def test_main_exits_without_importing_qt_when_wayland_never_appears(monkeypatch):
    monkeypatch.setattr(session_env, "ensure_session_environment", lambda: None)
    monkeypatch.setattr(session_env, "wait_for_wayland_socket", lambda **kwargs: False)
    assert tray.main() == 1
