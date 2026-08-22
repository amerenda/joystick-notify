"""Covers session_env.py's holistic fix, replacing three rounds of
individually-guessed environment variables (WAYLAND_DISPLAY, then
DISPLAY, then the XAUTHORITY gap that broke Steam a second time) with one
authoritative source: `systemctl --user show-environment`, which KDE's
session startup already populates correctly. See the module docstring
for the full history -- this is the fix for the actual root cause, not a
fourth guess.
"""
import os

import pytest

from joystick_notify.session_env import ensure_session_environment, parse_systemd_environment

# Real `systemctl --user show-environment` output captured live 2026-08-21
# on archlinux while diagnosing the XAUTHORITY gap -- this is what a real
# KDE Plasma Wayland session actually provides.
REAL_SYSTEMD_ENVIRONMENT = """\
HOME=/home/alex
LANG=en_US.UTF-8
LOGNAME=alex
PATH=/usr/local/bin:/usr/bin:/bin
SHELL=/usr/bin/zsh
USER=alex
XDG_RUNTIME_DIR=/run/user/1000
DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
DESKTOP_SESSION=/usr/share/wayland-sessions/plasma.desktop
DISPLAY=:0
KDE_FULL_SESSION=true
WAYLAND_DISPLAY=wayland-0
XAUTHORITY=/run/user/1000/xauth_MvttCq
XDG_CURRENT_DESKTOP=KDE
XDG_SESSION_CLASS=user
XDG_SESSION_DESKTOP=KDE
XDG_SESSION_ID=2
XDG_SESSION_TYPE=wayland
"""


@pytest.fixture(autouse=True)
def _clean_session_env(monkeypatch):
    for var in ("WAYLAND_DISPLAY", "XDG_SESSION_TYPE", "DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR"):
        monkeypatch.delenv(var, raising=False)


def test_parse_systemd_environment_extracts_all_keys():
    env = parse_systemd_environment(REAL_SYSTEMD_ENVIRONMENT)
    assert env["DISPLAY"] == ":0"
    assert env["WAYLAND_DISPLAY"] == "wayland-0"
    assert env["XAUTHORITY"] == "/run/user/1000/xauth_MvttCq"
    assert env["XDG_SESSION_TYPE"] == "wayland"
    assert len(env) == 18


def test_parse_systemd_environment_ignores_malformed_lines():
    assert parse_systemd_environment("no equals sign here\nDISPLAY=:0\n") == {"DISPLAY": ":0"}


def test_parse_systemd_environment_empty_output():
    assert parse_systemd_environment("") == {}


def test_ensure_session_environment_imports_full_systemd_environment(monkeypatch):
    # Direct regression test for the actual bug: XAUTHORITY must be picked
    # up from systemd's environment, not left for a guess that doesn't
    # exist (there was never a fallback default for XAUTHORITY -- it was
    # simply missing before this fix).
    monkeypatch.setattr(
        "joystick_notify.session_env._systemd_user_environment",
        lambda: parse_systemd_environment(REAL_SYSTEMD_ENVIRONMENT),
    )
    ensure_session_environment()
    assert os.environ["DISPLAY"] == ":0"
    assert os.environ["WAYLAND_DISPLAY"] == "wayland-0"
    assert os.environ["XAUTHORITY"] == "/run/user/1000/xauth_MvttCq"
    assert os.environ["XDG_SESSION_TYPE"] == "wayland"


def test_ensure_session_environment_does_not_override_already_set_values(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setattr(
        "joystick_notify.session_env._systemd_user_environment",
        lambda: parse_systemd_environment(REAL_SYSTEMD_ENVIRONMENT),
    )
    ensure_session_environment()
    assert os.environ["DISPLAY"] == ":99"


def test_ensure_session_environment_falls_back_when_systemd_environment_unavailable(monkeypatch):
    # Non-systemd system, or systemctl --user genuinely fails/unavailable.
    monkeypatch.setattr("joystick_notify.session_env._systemd_user_environment", lambda: {})
    ensure_session_environment()
    assert os.environ["WAYLAND_DISPLAY"] == "wayland-0"
    assert os.environ["DISPLAY"] == ":0"
    assert os.environ["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path=/run/user/{os.getuid()}/bus"
    # No fallback guess exists for XAUTHORITY -- if systemd's environment
    # isn't available, X11-auth-dependent apps are expected to fail
    # exactly as they would with no environment setup at all, not silently
    # get a wrong guess.
    assert "XAUTHORITY" not in os.environ


def test_systemd_user_environment_returns_empty_dict_when_systemctl_missing(monkeypatch):
    from joystick_notify import session_env

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("systemctl not found")

    monkeypatch.setattr(session_env.subprocess, "run", fake_run)
    assert session_env._systemd_user_environment() == {}
