import asyncio
from pathlib import Path

from joystick_notify.actions import audio
from joystick_notify.actions.audio import parse_sinks, resolve_hdmi_sink, resolve_sink_by_alsa, resolve_sink_by_description
from joystick_notify.config.schema import AudioConfig
from joystick_notify.health import Health, Status

PACTL_SAMPLE = """\
Sink #0
	State: RUNNING
	Name: alsa_output.pci-0000_03_00.1.hdmi-stereo
	Description: HDMI / DisplayPort - Couch TV
	Properties:
		alsa.card = "2"
		alsa.card_name = "HDA NVidia"
		alsa.device = "9"

Sink #1
	State: SUSPENDED
	Name: alsa_output.usb-SteelSeries_Arctis_Nova_7X-00.iec958-stereo
	Description: Arctis Nova 7X
	Properties:
		alsa.card = "3"
		alsa.device = "0"
"""


def test_parse_sinks_extracts_all_fields():
    sinks = parse_sinks(PACTL_SAMPLE)
    assert len(sinks) == 2
    assert sinks[0].name == "alsa_output.pci-0000_03_00.1.hdmi-stereo"
    assert sinks[0].description == "HDMI / DisplayPort - Couch TV"
    assert sinks[0].alsa_card == "2"
    assert sinks[0].alsa_device == "9"
    assert sinks[1].description == "Arctis Nova 7X"


def test_resolve_sink_by_alsa_match():
    sinks = parse_sinks(PACTL_SAMPLE)
    assert resolve_sink_by_alsa(sinks, "2", "9") == "alsa_output.pci-0000_03_00.1.hdmi-stereo"


def test_resolve_sink_by_alsa_no_match():
    sinks = parse_sinks(PACTL_SAMPLE)
    assert resolve_sink_by_alsa(sinks, "99", "99") is None


def test_resolve_sink_by_description():
    sinks = parse_sinks(PACTL_SAMPLE)
    assert resolve_sink_by_description(sinks, "Arctis Nova 7X") == "alsa_output.usb-SteelSeries_Arctis_Nova_7X-00.iec958-stereo"


def test_resolve_hdmi_sink_from_short_output():
    short = "0\talsa_output.pci-0000_03_00.1.hdmi-stereo\tmodule-alsa-card.c\ts16le 2ch 48000Hz\tSUSPENDED\n"
    assert resolve_hdmi_sink(short) == "alsa_output.pci-0000_03_00.1.hdmi-stereo"


def test_resolve_hdmi_sink_none_when_no_hdmi():
    short = "0\talsa_output.usb-SteelSeries-00.iec958-stereo\tmodule-alsa-card.c\ts16le\tSUSPENDED\n"
    assert resolve_hdmi_sink(short) is None


# --- resolve_couch_sink / activate_* verify-don't-trust ---
# Direct regression coverage for the 2026-08-21 live-testing finding:
# PipeWire's HDMI sink names carry a dynamically-assigned numeric suffix
# (...hdmi-stereo-extra2 vs ...extra3) that changes across reconnects. The
# configured couch_sink went stale between when it was saved and when the
# daemon used it, and the old code trusted it unconditionally -- routing
# to a sink that no longer existed while still reporting success.

CURRENT_SINKS_SAMPLE = """\
Sink #0
	State: IDLE
	Name: alsa_output.pci-0000_03_00.1.hdmi-stereo-extra2
	Description: HDMI / DisplayPort - Couch TV
	Properties:
		alsa.card = "2"
		alsa.device = "9"

Sink #1
	State: SUSPENDED
	Name: alsa_output.usb-SteelSeries_Arctis_Nova_7X-00.analog-stereo
	Description: Arctis Nova 7X
	Properties:
		alsa.card = "3"
		alsa.device = "0"
"""


def _patch_pactl(monkeypatch, responses):
    """responses: dict mapping a tuple of args to (rc, output)."""

    async def fake_pactl(*args, timeout=audio.PACTL_TIMEOUT_S):
        return responses.get(args, (0, ""))

    monkeypatch.setattr(audio, "_pactl", fake_pactl)


def test_resolve_couch_sink_trusts_configured_name_when_it_still_exists(monkeypatch):
    _patch_pactl(monkeypatch, {("list", "sinks"): (0, CURRENT_SINKS_SAMPLE)})
    cfg = AudioConfig(couch_sink="alsa_output.pci-0000_03_00.1.hdmi-stereo-extra2")
    result = asyncio.run(audio.resolve_couch_sink(cfg))
    assert result == "alsa_output.pci-0000_03_00.1.hdmi-stereo-extra2"


def test_resolve_couch_sink_falls_back_when_configured_name_is_stale(monkeypatch):
    short_sinks = "0\talsa_output.pci-0000_03_00.1.hdmi-stereo-extra2\tmod\ts16le\tIDLE\n"
    _patch_pactl(
        monkeypatch,
        {
            ("list", "sinks"): (0, CURRENT_SINKS_SAMPLE),
            ("list", "short", "sinks"): (0, short_sinks),
        },
    )
    # Configured name has the OLD suffix that no longer exists.
    cfg = AudioConfig(couch_sink="alsa_output.pci-0000_03_00.1.hdmi-stereo-extra3")
    result = asyncio.run(audio.resolve_couch_sink(cfg))
    # Falls through to the dynamic HDMI-name match, which only ever
    # returns names pulled from pactl's live output.
    assert result == "alsa_output.pci-0000_03_00.1.hdmi-stereo-extra2"


def test_set_default_sink_returns_false_on_failure(monkeypatch):
    _patch_pactl(monkeypatch, {("set-default-sink", "nonexistent"): (1, "Failure: No such entity")})
    result = asyncio.run(audio.set_default_sink("nonexistent"))
    assert result is False


def test_set_default_sink_returns_true_on_success(monkeypatch):
    _patch_pactl(monkeypatch, {("set-default-sink", "real-sink"): (0, "")})
    result = asyncio.run(audio.set_default_sink("real-sink"))
    assert result is True


def test_activate_couch_reports_degraded_not_ok_when_set_fails(tmp_path, monkeypatch):
    _patch_pactl(
        monkeypatch,
        {
            ("list", "sinks"): (0, CURRENT_SINKS_SAMPLE),
            ("set-default-sink", "alsa_output.pci-0000_03_00.1.hdmi-stereo-extra2"): (1, "Failure: No such entity"),
            ("list", "short", "sink-inputs"): (0, ""),
        },
    )
    cfg = AudioConfig(couch_sink="alsa_output.pci-0000_03_00.1.hdmi-stereo-extra2")
    health = Health(path=Path(tmp_path) / "health.json")
    asyncio.run(audio.activate_couch(cfg, health))
    assert health.get("audio").status == Status.DEGRADED


def test_activate_desk_reports_degraded_when_configured_sink_missing(tmp_path, monkeypatch):
    _patch_pactl(monkeypatch, {("list", "sinks"): (0, CURRENT_SINKS_SAMPLE)})
    cfg = AudioConfig(desk_sink="alsa_output.usb-SteelSeries_Arctis_Nova_7X-00.iec958-stereo")  # stale, real one is analog-stereo
    health = Health(path=Path(tmp_path) / "health.json")
    asyncio.run(audio.activate_desk(cfg, health))
    status = health.get("audio")
    assert status.status == Status.DEGRADED
    assert "not found" in status.reason


def test_activate_desk_succeeds_when_configured_sink_exists(tmp_path, monkeypatch):
    _patch_pactl(
        monkeypatch,
        {
            ("list", "sinks"): (0, CURRENT_SINKS_SAMPLE),
            ("set-default-sink", "alsa_output.usb-SteelSeries_Arctis_Nova_7X-00.analog-stereo"): (0, ""),
            ("list", "short", "sink-inputs"): (0, ""),
        },
    )
    cfg = AudioConfig(desk_sink="alsa_output.usb-SteelSeries_Arctis_Nova_7X-00.analog-stereo")
    health = Health(path=Path(tmp_path) / "health.json")
    asyncio.run(audio.activate_desk(cfg, health))
    assert health.get("audio").status == Status.OK
