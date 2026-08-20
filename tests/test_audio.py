from joystick_notify.actions.audio import parse_sinks, resolve_hdmi_sink, resolve_sink_by_alsa, resolve_sink_by_description

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
