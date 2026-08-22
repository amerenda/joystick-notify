import asyncio

from joystick_notify.actions.cec_control import parse_power_status
from joystick_notify.devices.cec import (
    find_audio_system_target,
    get_topology,
    parse_own_physical_address,
    parse_topology,
)
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


class _FakeProc:
    def __init__(self, out: bytes, returncode: int = 0):
        self._out = out
        self.returncode = returncode

    async def communicate(self):
        return self._out, b""


def test_get_topology_parses_real_subprocess_output(monkeypatch):
    async def fake_exec(*args, **kwargs):
        assert args[0] == "cec-ctl"
        return _FakeProc(DRIVER_INFO_SAMPLE.encode())

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    devices = asyncio.run(get_topology("/dev/cec0"))
    assert len(devices) == 2
    assert devices[0].device_type == "TV"


def test_get_topology_returns_empty_when_cec_ctl_missing(monkeypatch):
    async def fake_exec(*args, **kwargs):
        raise FileNotFoundError("cec-ctl not found")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    assert asyncio.run(get_topology(None)) == []


def test_get_topology_returns_empty_on_nonzero_exit(monkeypatch):
    async def fake_exec(*args, **kwargs):
        return _FakeProc(b"some error", returncode=1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    assert asyncio.run(get_topology("/dev/cec0")) == []


def test_standby_and_verify_logs_warning_when_unconfirmed(tmp_path, caplog):
    # Direct regression test: the final degraded outcome previously had no
    # log line of its own -- only the per-attempt INFO lines and a silent
    # Health.degraded() call -- so it never showed up in the event log at
    # a level anyone would notice while troubleshooting "TV didn't turn off."
    from joystick_notify.actions import cec_control

    async def fake_run(cmd, timeout=5.0):
        return 0, "pwr-state: on"

    orig_run = cec_control._run
    cec_control._run = fake_run
    try:
        health = Health(path=Path(tmp_path) / "health.json")
        with caplog.at_level("WARNING", logger="joystick_notify.actions.cec_control"):
            asyncio.run(cec_control.standby_and_verify(None, [0, 5], health, attempts=1, delay_s=0))
        assert any("unconfirmed" in r.message for r in caplog.records)
    finally:
        cec_control._run = orig_run


def test_standby_and_verify_reports_ok_on_separate_component_when_unconfirmed(tmp_path):
    # Direct regression test: this used to report health.degraded("cec", ...)
    # -- the SAME component name check_startup_health()/ensure_adapter()
    # use for "is the adapter/driver present" -- so a TV simply not
    # responding to standby (a real, common CEC quirk, not a daemon
    # problem) made the whole daemon look unhealthy. It's now a separate
    # "cec_standby" component, reported ok() rather than degraded() per
    # explicit feedback: this isn't the kind of thing that should read as
    # "unhealthy" the way a missing adapter/driver is.
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
        assert health.get("cec") is None  # adapter-presence component untouched
        status = health.get("cec_standby")
        assert status is not None
        assert status.status == Status.OK
        assert "unconfirmed" in status.reason
    finally:
        cec_control._run = orig_run


def test_standby_and_verify_reports_ok_on_separate_component_when_confirmed(tmp_path):
    from joystick_notify.actions import cec_control

    async def fake_run(cmd, timeout=5.0):
        return 0, "pwr-state: standby"

    orig_run = cec_control._run
    cec_control._run = fake_run
    try:
        health = Health(path=Path(tmp_path) / "health.json")
        unconfirmed = asyncio.run(
            cec_control.standby_and_verify(None, [0, 5], health, attempts=1, delay_s=0)
        )
        assert unconfirmed == []
        assert health.get("cec") is None
        status = health.get("cec_standby")
        assert status is not None
        assert status.status == Status.OK
        assert "confirmed" in status.reason
    finally:
        cec_control._run = orig_run


def test_standby_and_verify_reclaims_active_source_before_every_attempt_when_phys_addr_set(tmp_path):
    # Direct regression test for the real root cause found live 2026-08-22:
    # this TV (and independently the receiver) silently no-op <Standby>
    # from a device they no longer consider the Active Source -- a plain
    # `--standby` Tx's OK at the bus level but produces zero power-state
    # change, while the exact same Standby immediately preceded by
    # reclaiming Active Source (Set Stream Path + Active Source) reliably
    # works. Reclaiming must happen before EVERY attempt, not just once,
    # since another CEC device on the bus (an Nvidia Shield here) can
    # reclaim Active Source again mid-session.
    from joystick_notify.actions import cec_control

    calls = []

    async def fake_run(cmd, timeout=5.0):
        calls.append(cmd)
        return 0, "pwr-state: on"

    orig_run = cec_control._run
    cec_control._run = fake_run
    try:
        health = Health(path=Path(tmp_path) / "health.json")
        asyncio.run(
            cec_control.standby_and_verify(
                None, [0], health, phys_addr="3.2.0.0", attempts=2, delay_s=0
            )
        )
        # Each attempt: set-stream-path, active-source, standby, then the
        # power-status poll -- 4 commands per attempt, 2 attempts = 8.
        assert len(calls) == 8
        opcodes = [next(a for a in cmd if a.startswith("--") and a != "--to") for cmd in calls]
        assert opcodes == [
            "--set-stream-path", "--active-source", "--standby", "--give-device-power-status",
            "--set-stream-path", "--active-source", "--standby", "--give-device-power-status",
        ]
    finally:
        cec_control._run = orig_run


def test_standby_and_verify_skips_reclaim_when_no_phys_addr_configured(tmp_path):
    from joystick_notify.actions import cec_control

    calls = []

    async def fake_run(cmd, timeout=5.0):
        calls.append(cmd)
        return 0, "pwr-state: standby"

    orig_run = cec_control._run
    cec_control._run = fake_run
    try:
        health = Health(path=Path(tmp_path) / "health.json")
        asyncio.run(cec_control.standby_and_verify(None, [0], health, attempts=1, delay_s=0))
        # Just --standby then the power-status poll -- no reclaim commands.
        assert len(calls) == 2
        assert "--standby" in calls[0]
        assert "--give-device-power-status" in calls[1]
    finally:
        cec_control._run = orig_run
