"""Covers the two real bugs found via live testing 2026-08-21:

1. kscreen-doctor aborted (SIGABRT) with WAYLAND_DISPLAY unset.
2. Steam's Big Picture launch failed ("unable to open a connection to X")
   because the first fix treated Wayland/X11 as mutually exclusive and
   never set DISPLAY on this Wayland-primary-but-XWayland-present session.

Both display variables are now defaulted unconditionally (never
overriding an already-set value) rather than inferring one exclusive
"session type" and picking only one to default.
"""
import os

import pytest

from joystick_notify.session_env import ensure_session_environment


@pytest.fixture(autouse=True)
def _clean_session_env(monkeypatch):
    for var in ("WAYLAND_DISPLAY", "XDG_SESSION_TYPE", "DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "XDG_RUNTIME_DIR"):
        monkeypatch.delenv(var, raising=False)
    # Point at paths with no real sockets, so the XDG_SESSION_TYPE
    # inference (informational only) is deterministic in tests regardless
    # of what's actually running on the machine running the test suite.
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/nonexistent-for-tests")


def test_defaults_both_display_variables_when_unset():
    ensure_session_environment()
    assert os.environ["WAYLAND_DISPLAY"] == "wayland-0"
    assert os.environ["DISPLAY"] == ":0"


def test_does_not_override_existing_wayland_display(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    ensure_session_environment()
    assert os.environ["WAYLAND_DISPLAY"] == "wayland-1"


def test_does_not_override_existing_display(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":1")
    ensure_session_environment()
    assert os.environ["DISPLAY"] == ":1"


def test_sets_both_even_when_only_one_was_already_set(monkeypatch):
    # Direct regression test for bug #2: WAYLAND_DISPLAY already being
    # correct must not prevent DISPLAY from also getting defaulted --
    # Steam needs DISPLAY even on an otherwise-working Wayland session.
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    ensure_session_environment()
    assert os.environ["WAYLAND_DISPLAY"] == "wayland-0"
    assert os.environ["DISPLAY"] == ":0"


def test_does_not_override_existing_dbus_session_bus_address(monkeypatch):
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/custom/bus")
    ensure_session_environment()
    assert os.environ["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/custom/bus"


def test_sets_dbus_session_bus_address_default_when_unset():
    ensure_session_environment()
    assert os.environ["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path=/run/user/{os.getuid()}/bus"


def test_xdg_session_type_informational_only_does_not_gate_display_vars(monkeypatch):
    # Whatever XDG_SESSION_TYPE ends up as, both display variables are
    # still defaulted -- nothing branches on it.
    monkeypatch.setenv("XDG_SESSION_TYPE", "tty")  # e.g. an SSH session, per systemd-logind
    ensure_session_environment()
    assert os.environ["WAYLAND_DISPLAY"] == "wayland-0"
    assert os.environ["DISPLAY"] == ":0"
