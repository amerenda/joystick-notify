"""Covers the 2026-08-21 live-testing finding (kscreen-doctor aborts
without WAYLAND_DISPLAY set) and the follow-up: v1's hardcoded
"XDG_SESSION_TYPE=wayland" would be wrong on a real X11 session, so this
must infer the actual session type rather than assume Wayland
unconditionally.
"""
import os

import pytest

from joystick_notify.session_env import ensure_session_environment


@pytest.fixture(autouse=True)
def _clean_session_env(monkeypatch):
    for var in ("WAYLAND_DISPLAY", "XDG_SESSION_TYPE", "DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "XDG_RUNTIME_DIR"):
        monkeypatch.delenv(var, raising=False)
    # Point XDG_RUNTIME_DIR somewhere with no wayland-0 socket, so
    # _wayland_socket_present() is deterministic in tests regardless of
    # what's actually running on the machine running the test suite.
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/nonexistent-for-tests")


def test_infers_wayland_when_wayland_display_already_set(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    ensure_session_environment()
    assert os.environ["XDG_SESSION_TYPE"] == "wayland"
    assert os.environ["WAYLAND_DISPLAY"] == "wayland-1"  # not overridden
    assert "DISPLAY" not in os.environ


def test_infers_x11_when_display_already_set(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":1")
    ensure_session_environment()
    assert os.environ["XDG_SESSION_TYPE"] == "x11"
    assert os.environ["DISPLAY"] == ":1"  # not overridden
    assert "WAYLAND_DISPLAY" not in os.environ


def test_respects_explicit_x11_session_type_and_defaults_display(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    ensure_session_environment()
    assert os.environ["DISPLAY"] == ":0"
    assert "WAYLAND_DISPLAY" not in os.environ


def test_respects_explicit_wayland_session_type_and_defaults_wayland_display(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    ensure_session_environment()
    assert os.environ["WAYLAND_DISPLAY"] == "wayland-0"
    assert "DISPLAY" not in os.environ


def test_falls_back_to_wayland_when_nothing_detectable():
    # No WAYLAND_DISPLAY, no DISPLAY, no XDG_SESSION_TYPE, no wayland
    # socket present -- matches v1's original assumption as the
    # last-resort default.
    ensure_session_environment()
    assert os.environ["XDG_SESSION_TYPE"] == "wayland"
    assert os.environ["WAYLAND_DISPLAY"] == "wayland-0"


def test_does_not_override_existing_dbus_session_bus_address(monkeypatch):
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/custom/bus")
    ensure_session_environment()
    assert os.environ["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/custom/bus"


def test_sets_dbus_session_bus_address_default_when_unset():
    ensure_session_environment()
    assert os.environ["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path=/run/user/{os.getuid()}/bus"
