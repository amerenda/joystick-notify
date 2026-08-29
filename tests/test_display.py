from pathlib import Path

from joystick_notify.actions.display import connector_status, output_enabled, parse_outputs

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
