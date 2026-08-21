"""Regression test for the 2026-08-21 live-testing finding: a plain SSH
shell / systemd --user context does not reliably inherit WAYLAND_DISPLAY
on this box (confirmed: kscreen-doctor aborted with SIGABRT and zero
output without it), even though DBUS_SESSION_BUS_ADDRESS is inherited
correctly. ensure_session_environment() must default WAYLAND_DISPLAY
(and the other vars v1's config-env.sh exported) without ever overriding
a value that's actually already set.
"""
from joystick_notify.session_env import ensure_session_environment


def test_sets_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)

    ensure_session_environment()

    import os

    assert os.environ["WAYLAND_DISPLAY"] == "wayland-0"
    assert os.environ["XDG_SESSION_TYPE"] == "wayland"
    assert os.environ["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path=/run/user/{os.getuid()}/bus"


def test_does_not_override_existing_values(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/custom/bus")

    ensure_session_environment()

    import os

    assert os.environ["WAYLAND_DISPLAY"] == "wayland-1"
    assert os.environ["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/custom/bus"
