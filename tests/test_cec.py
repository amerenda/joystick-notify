import asyncio

from joystick_notify.actions.cec_control import parse_power_status
from joystick_notify.devices.cec import find_audio_system_target, parse_own_physical_address, parse_topology
from joystick_notify.health import Health, Status
from pathlib import Path


DRIVER_INFO_SAMPLE = """\
Driver Info:
	API Version                 : 6.1.0
	Driver                      : cec-gpio
	Capabilities                : 0x0000043f
		Physical Address
		Logical Addresses
	Physical Address             : 4.0.0.0
	Logical Address Mask         : 0x0000
Topology:
	System Information for device 0 (TV):
		Physical Address                    : 0.0.0.0
		OSD Name                            : LG OLED
	System Information for device 5 (Audio System):
		Physical Address                    : 3.0.0.0
		OSD Name                            : Receiver
"""


def test_parse_own_physical_address_takes_first_match_not_topology():
    # Regression test for v1's real 2026-08-16 bug: a naive scan of the
    # whole output for any dotted-quad after "Playback" would have picked up
    # a different device entirely if one existed below. The first "Physical
    # Address" line (Driver Info block) must win.
    assert parse_own_physical_address(DRIVER_INFO_SAMPLE) == "4.0.0.0"


def test_parse_own_physical_address_skips_bare_capability_bullet():
    output = "Capabilities:\n\tPhysical Address\n\tLogical Addresses\nPhysical Address             : 2.0.0.0\n"
    assert parse_own_physical_address(output) == "2.0.0.0"


def test_parse_own_physical_address_none_when_absent():
    assert parse_own_physical_address("no relevant output here") is None


def test_parse_topology_extracts_devices():
    devices = parse_topology(DRIVER_INFO_SAMPLE)
    assert len(devices) == 2
    assert devices[0].logical_address == 0
    assert devices[0].device_type == "TV"
    assert devices[0].phys_addr == "0.0.0.0"
    assert devices[1].device_type == "Audio System"
    assert devices[1].phys_addr == "3.0.0.0"


def test_find_audio_system_target():
    devices = parse_topology(DRIVER_INFO_SAMPLE)
    target = find_audio_system_target(devices)
    assert target is not None
    assert target.logical_address == 5


def test_find_audio_system_target_none_when_no_receiver():
    devices = parse_topology("System Information for device 0 (TV):\n\tPhysical Address : 0.0.0.0\n")
    assert find_audio_system_target(devices) is None


def test_parse_power_status_standby():
    assert parse_power_status("pwr-state: standby") == "standby"


def test_parse_power_status_on():
    assert parse_power_status("pwr-state: on") == "on"


def test_parse_power_status_unknown_on_garbage():
    assert parse_power_status("no meaningful output") == "unknown"


def test_standby_and_verify_reports_degraded_when_unconfirmed(tmp_path):
    from joystick_notify.actions import cec_control

    async def fake_run(cmd, timeout=5.0):
        # Simulate cec-ctl always reporting "on" (never confirms standby).
        return 0, "pwr-state: on"

    orig_run = cec_control._run
    cec_control._run = fake_run
    try:
        health = Health(path=Path(tmp_path) / "health.json")
        unconfirmed = asyncio.run(
            cec_control.standby_and_verify(None, [0, 5], health, attempts=1, delay_s=0)
        )
        assert unconfirmed == [0, 5]
        assert health.get("cec").status == Status.DEGRADED
    finally:
        cec_control._run = orig_run
