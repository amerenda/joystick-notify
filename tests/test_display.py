import asyncio
from pathlib import Path

import pytest

from joystick_notify.actions import display as display_module
from joystick_notify.actions.display import connector_status, detect_active_mode, output_enabled, parse_outputs
from joystick_notify.config.schema import DisplayConfig
from joystick_notify.state_machine import Mode

KSCREEN_JSON = {
    "outputs": [
        {
            "name": "HDMI-A-1",
            "enabled": True,
            "connected": True,
            "edid": {"name": "LG OLED55"},
            "preferredModeId": "1",
            "modes": [{"id": "1", "preferred": True, "size": {"width": 3840, "height": 2160}, "refreshRate": 60}],
        },
        {
            "name": "HDMI-A-2",
            "enabled": False,
            "connected": True,
            "edid": {"name": "Dell U2723QE"},
            "modes": [],
        },
    ]
}


def _fake_kscreen_json(value):
    async def fake():
        return value

    return fake


def test_parse_outputs_extracts_model_and_preferred_mode():
    outputs = parse_outputs(KSCREEN_JSON)
    assert outputs[0].name == "HDMI-A-1"
    assert outputs[0].model == "LG OLED55"
    assert outputs[0].preferred_mode == "3840x2160@60"
    assert outputs[1].enabled is False


def test_output_enabled_true_for_active_port():
    assert output_enabled(KSCREEN_JSON, "HDMI-A-1") is True


def test_output_enabled_false_for_disabled_port():
    assert output_enabled(KSCREEN_JSON, "HDMI-A-2") is False


def test_output_enabled_false_for_unknown_port():
    assert output_enabled(KSCREEN_JSON, "HDMI-A-99") is False


@pytest.mark.asyncio
async def test_detect_active_mode_returns_couch_when_couch_port_enabled(monkeypatch):
    monkeypatch.setattr(display_module, "get_kscreen_json", _fake_kscreen_json(KSCREEN_JSON))
    config = DisplayConfig(desk_port="HDMI-A-2", couch_port="HDMI-A-1")
    assert await detect_active_mode(config) == Mode.COUCH


@pytest.mark.asyncio
async def test_detect_active_mode_returns_desk_when_desk_port_enabled(monkeypatch):
    monkeypatch.setattr(display_module, "get_kscreen_json", _fake_kscreen_json(KSCREEN_JSON))
    config = DisplayConfig(desk_port="HDMI-A-1", couch_port="HDMI-A-2")
    assert await detect_active_mode(config) == Mode.DESK


@pytest.mark.asyncio
async def test_detect_active_mode_none_when_kscreen_doctor_unreachable(monkeypatch):
    monkeypatch.setattr(display_module, "get_kscreen_json", _fake_kscreen_json(None))
    config = DisplayConfig(desk_port="HDMI-A-2", couch_port="HDMI-A-1")
    assert await detect_active_mode(config) is None


@pytest.mark.asyncio
async def test_detect_active_mode_none_when_neither_port_enabled(monkeypatch):
    both_disabled = {
        "outputs": [
            {"name": "HDMI-A-1", "enabled": False, "connected": True},
            {"name": "HDMI-A-2", "enabled": False, "connected": True},
        ]
    }
    monkeypatch.setattr(display_module, "get_kscreen_json", _fake_kscreen_json(both_disabled))
    config = DisplayConfig(desk_port="HDMI-A-2", couch_port="HDMI-A-1")
    assert await detect_active_mode(config) is None


def test_connector_status_connected(tmp_path):
    card_dir = Path(tmp_path) / "card1-HDMI-A-1"
    card_dir.mkdir()
    (card_dir / "status").write_text("connected\n")
    assert connector_status("HDMI-A-1", sysfs_root=tmp_path) == "connected"


def test_connector_status_disconnected(tmp_path):
    card_dir = Path(tmp_path) / "card1-HDMI-A-1"
    card_dir.mkdir()
    (card_dir / "status").write_text("disconnected\n")
    assert connector_status("HDMI-A-1", sysfs_root=tmp_path) == "disconnected"


def test_connector_status_unknown_when_missing(tmp_path):
    assert connector_status("HDMI-A-9", sysfs_root=tmp_path) == "unknown"


# Real kscreen-doctor -j output shape, captured live 2026-08-21 (KDE Plasma
# on archlinux): edid is null (not an {"name": ...} dict), there is no
# per-mode "preferred" boolean and no "preferredModeId", only a
# "preferredModes" list of mode ids. All of this differs from the
# originally-assumed shape in KSCREEN_JSON above.
REAL_KSCREEN_JSON = {
    "outputs": [
        {
            "name": "HDMI-A-1",
            "enabled": True,
            "connected": True,
            "edid": None,
            "currentModeId": "2",
            "preferredModes": ["2"],
            "modes": [
                {"id": "2", "name": "3840x2160@60", "refreshRate": 60.0, "size": {"width": 3840, "height": 2160}},
                {"id": "3", "name": "1920x1080@60", "refreshRate": 60.0, "size": {"width": 1920, "height": 1080}},
            ],
        },
        {
            "name": "HDMI-A-2",
            "enabled": False,
            "connected": True,
            "edid": None,
            "currentModeId": "1",
            "preferredModes": [],
            "modes": [{"id": "1", "name": "2560x1440@60", "refreshRate": 59.951, "size": {"width": 2560, "height": 1440}}],
        },
    ]
}


def test_parse_outputs_real_shape_uses_preferred_modes_list():
    outputs = parse_outputs(REAL_KSCREEN_JSON)
    assert outputs[0].preferred_mode == "3840x2160@60"
    assert outputs[0].model == ""  # edid is null on this system — degrades gracefully, not a crash


def test_parse_outputs_real_shape_falls_back_to_current_mode_when_no_preferred():
    # HDMI-A-2 has an empty preferredModes list — must still surface
    # *some* sane mode default (what's currently active) rather than a
    # blank field in the wizard.
    outputs = parse_outputs(REAL_KSCREEN_JSON)
    assert outputs[1].preferred_mode == "2560x1440@60"


def test_dpms_states_parses_kscreen_doctor_show_output(monkeypatch):
    async def fake_run(cmd, timeout=None):
        assert cmd == ["kscreen-doctor", "--dpms", "show"]
        return 0, "dpms mode for screen HDMI-A-1: on\ndpms mode for screen HDMI-A-2: off\n"

    monkeypatch.setattr(display_module, "_run", fake_run)

    states = asyncio.run(display_module.dpms_states())

    assert states == {"HDMI-A-1": "on", "HDMI-A-2": "off"}


def test_dpms_states_returns_empty_dict_on_command_failure(monkeypatch):
    async def fake_run(cmd, timeout=None):
        return -1, "kscreen-doctor: command not found"

    monkeypatch.setattr(display_module, "_run", fake_run)

    assert asyncio.run(display_module.dpms_states()) == {}


def test_restore_dpms_off_is_a_noop_when_nothing_was_off(monkeypatch):
    calls = []

    async def fake_run(cmd, timeout=None):
        calls.append(cmd)
        return 0, ""

    monkeypatch.setattr(display_module, "_run", fake_run)

    asyncio.run(display_module.restore_dpms_off(set()))

    assert calls == []


def test_restore_dpms_off_excludes_outputs_that_were_already_on(monkeypatch):
    # Regression test for the 2026-09-03 fix: SimulateUserActivity (part
    # of screen_lock.py's unlock/relock flow) resets DPMS's idle timer
    # too, which can wake an intentionally-off desk monitor as a side
    # effect of an otherwise unrelated screen-unlock call (Sunshine's
    # stream-start hook, in particular). restore_dpms_off must turn only
    # the previously-off outputs back off, leaving already-on ones alone.
    calls = []

    async def fake_run(cmd, timeout=None):
        calls.append(cmd)
        if cmd == ["kscreen-doctor", "--dpms", "show"]:
            return 0, "dpms mode for screen HDMI-A-1: on\ndpms mode for screen HDMI-A-2: on\n"
        return 0, ""

    monkeypatch.setattr(display_module, "_run", fake_run)

    asyncio.run(display_module.restore_dpms_off({"HDMI-A-2"}))

    assert calls[0] == ["kscreen-doctor", "--dpms", "show"]
    off_cmd = calls[1]
    assert off_cmd[:3] == ["kscreen-doctor", "--dpms", "off"]
    assert "--dpms-excluded" in off_cmd and "HDMI-A-1" in off_cmd
    assert "HDMI-A-2" not in off_cmd  # the one we WANT set off is not excluded
