import asyncio
from pathlib import Path

import pytest

from joystick_notify.actions import display as display_module
from joystick_notify.actions.display import connector_status, detect_active_mode, output_enabled, parse_outputs
from joystick_notify.config.schema import DisplayConfig
from joystick_notify.state_machine import MIXED_LAYOUT, Mode

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
async def test_detect_active_mode_mixed_when_neither_port_enabled(monkeypatch):
    both_disabled = {
        "outputs": [
            {"name": "HDMI-A-1", "enabled": False, "connected": True},
            {"name": "HDMI-A-2", "enabled": False, "connected": True},
        ]
    }
    monkeypatch.setattr(display_module, "get_kscreen_json", _fake_kscreen_json(both_disabled))
    config = DisplayConfig(desk_port="HDMI-A-2", couch_port="HDMI-A-1")
    assert await detect_active_mode(config) == MIXED_LAYOUT


@pytest.mark.asyncio
async def test_detect_active_mode_mixed_when_both_ports_enabled(monkeypatch):
    # 2026-10-21: KWin enabled the TV next to the desk after a reboot.
    both_enabled = {
        "outputs": [
            {"name": "HDMI-A-1", "enabled": True, "connected": True},
            {"name": "HDMI-A-2", "enabled": True, "connected": True},
        ]
    }
    monkeypatch.setattr(display_module, "get_kscreen_json", _fake_kscreen_json(both_enabled))
    config = DisplayConfig(desk_port="HDMI-A-2", couch_port="HDMI-A-1")
    assert await detect_active_mode(config) == MIXED_LAYOUT


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


# --- Bad display config and blind retries (issues #52, #53) ---

from joystick_notify.actions.display import layout_summary, mode_problem, output_modes
from joystick_notify.config.schema import display_problems
from joystick_notify.health import Health
from joystick_notify.state_machine import ActivationError

TV_AND_MONITOR = {
    "outputs": [
        {"id": 2, "name": "HDMI-A-1", "enabled": False, "connected": True,
         "modes": [{"id": "10", "name": "3840x2160@60"}, {"id": "11", "name": "1920x1080@60"}]},
        {"id": 1, "name": "HDMI-A-2", "enabled": True, "connected": True,
         "modes": [{"id": "2", "name": "2560x1440@144"}]},
    ]
}


def test_display_problems_rejects_identical_couch_and_desk_ports():
    problems = display_problems(DisplayConfig(desk_port="HDMI-A-2", couch_port="HDMI-A-2"))
    assert len(problems) == 1 and "HDMI-A-2" in problems[0]


def test_display_problems_accepts_distinct_and_unconfigured_ports():
    assert display_problems(DisplayConfig(desk_port="HDMI-A-2", couch_port="HDMI-A-1")) == []
    assert display_problems(DisplayConfig()) == []


def test_output_modes_lists_labels_and_none_for_unknown_output():
    assert output_modes(TV_AND_MONITOR, "HDMI-A-1") == ["3840x2160@60", "1920x1080@60"]
    assert output_modes(TV_AND_MONITOR, "DP-1") is None


def test_mode_problem_flags_a_mode_the_output_does_not_list():
    # The 2026-10-27 case: the desk monitor was asked for the TV's 4K mode.
    msg = mode_problem(TV_AND_MONITOR, "HDMI-A-2", "3840x2160@60")
    assert msg and "HDMI-A-2" in msg and "3840x2160@60" in msg and "2560x1440@144" in msg


def test_mode_problem_none_when_listed_or_unknowable():
    assert mode_problem(TV_AND_MONITOR, "HDMI-A-1", "3840x2160@60") is None
    assert mode_problem(None, "HDMI-A-1", "3840x2160@60") is None
    assert mode_problem(TV_AND_MONITOR, "DP-1", "3840x2160@60") is None  # not on the bus: cannot tell
    assert mode_problem(TV_AND_MONITOR, "HDMI-A-1", "") is None


def test_layout_summary_names_ids_and_state():
    assert layout_summary(TV_AND_MONITOR) == "HDMI-A-1 id=2 disabled; HDMI-A-2 id=1 enabled"
    assert layout_summary(None) == "kscreen-doctor -j unavailable"


@pytest.fixture
def fast_display(monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr(display_module.asyncio, "sleep", no_sleep)
    rescans = []

    async def rescan(port, sysfs_root="/sys/class/drm"):
        rescans.append(port)

    monkeypatch.setattr(display_module, "trigger_connector_rescan", rescan)
    return rescans


def _scripted_kscreen(monkeypatch, snapshots):
    """_run returns rc=0 every time; get_kscreen_json walks `snapshots`."""
    calls = []

    async def fake_run(cmd, timeout=0):
        calls.append(cmd)
        return 0, "Output 1 set mode"

    state = {"i": 0}

    async def fake_json():
        snap = snapshots[min(state["i"], len(snapshots) - 1)]
        state["i"] += 1
        return snap

    monkeypatch.setattr(display_module, "_run", fake_run)
    monkeypatch.setattr(display_module, "get_kscreen_json", fake_json)
    return calls


async def test_apply_stops_retrying_a_mode_the_output_does_not_list(monkeypatch, fast_display, caplog):
    tv_on = {"outputs": [dict(TV_AND_MONITOR["outputs"][0], enabled=True), dict(TV_AND_MONITOR["outputs"][1], enabled=False)]}
    calls = _scripted_kscreen(monkeypatch, [tv_on])
    ok, layout = await display_module._apply_and_verify_detail("HDMI-A-2", "3840x2160@60", "HDMI-A-1", max_attempts=5, retry_delay_s=0)
    assert ok is False
    assert len(calls) == 1  # not 5 identical commands
    assert "HDMI-A-1 id=2 enabled" in layout
    assert any("does not list 3840x2160@60" in r.message for r in caplog.records)


async def test_apply_failure_logs_the_live_layout(monkeypatch, fast_display, caplog):
    _scripted_kscreen(monkeypatch, [TV_AND_MONITOR])
    await display_module._apply_and_verify_detail("HDMI-A-1", "3840x2160@60", "HDMI-A-2", max_attempts=2, retry_delay_s=0)
    warnings = [r.message for r in caplog.records if "did not take" in r.message]
    assert len(warnings) == 2 and all("live layout: HDMI-A-1 id=2 disabled; HDMI-A-2 id=1 enabled" in w for w in warnings)


async def test_apply_rescans_when_the_output_left_the_bus_between_attempts(monkeypatch, fast_display):
    gone = {"outputs": [TV_AND_MONITOR["outputs"][1]]}
    _scripted_kscreen(monkeypatch, [gone, TV_AND_MONITOR])
    await display_module._apply_and_verify_detail("HDMI-A-1", "3840x2160@60", "HDMI-A-2", max_attempts=2, retry_delay_s=0)
    assert fast_display == ["HDMI-A-1"]


async def test_apply_succeeds_once_the_output_is_enabled(monkeypatch, fast_display):
    enabled = {"outputs": [dict(TV_AND_MONITOR["outputs"][0], enabled=True), dict(TV_AND_MONITOR["outputs"][1], enabled=False)]}
    _scripted_kscreen(monkeypatch, [TV_AND_MONITOR, enabled])
    ok, layout = await display_module._apply_and_verify_detail("HDMI-A-1", "3840x2160@60", "HDMI-A-2", max_attempts=3, retry_delay_s=0)
    assert ok is True and layout == "HDMI-A-1 id=2 enabled; HDMI-A-2 id=1 disabled"


async def test_activate_desk_reports_live_layout_when_it_fails(monkeypatch, fast_display):
    _scripted_kscreen(monkeypatch, [TV_AND_MONITOR])
    health = Health()
    # desk output never becomes enabled in the scripted layout
    await display_module.activate_desk(DisplayConfig(desk_port="HDMI-A-1", couch_port="HDMI-A-2", desk_mode="1920x1080@60", couch_mode="2560x1440@144"), health)
    status = health.get("display")
    assert status.status.value == "failed" and "live layout" in status.detail


async def test_activate_couch_and_desk_refuse_identical_ports(monkeypatch, fast_display):
    calls = _scripted_kscreen(monkeypatch, [TV_AND_MONITOR])
    cfg = DisplayConfig(desk_port="HDMI-A-2", couch_port="HDMI-A-2", desk_mode="2560x1440@144", couch_mode="2560x1440@144")
    health = Health()
    await display_module.activate_desk(cfg, health)
    assert health.get("display").status.value == "failed" and calls == []
    with pytest.raises(ActivationError):
        await display_module.activate_couch(cfg, health)
    assert calls == []
