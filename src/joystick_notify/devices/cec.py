"""CEC adapter discovery: kernel /dev/cec* enumeration + not-yet-attached
USB Pulse-Eight-protocol dongle scanning, and parsing of `cec-ctl -S`
output. Pure parsing functions are unit-testable without hardware; the
`discover_*` functions touch the filesystem and are exercised for real
only on a box with actual CEC hardware.

`parse_own_physical_address()` is a direct, deliberately-conservative port
of v1's `get_cec_phys_addr` (lib/cec-control.sh): take the FIRST "Physical
Address" line in `cec-ctl -d <dev> -S` output. v1 found the hard way
(2026-08-16) that `--skip-info` hides this and that scanning the topology
dump for "the first Playback Device" breaks as soon as a second Playback
Device exists on the bus (e.g. an Nvidia Shield behind a receiver) — this
exact bug silently pointed Active Source at someone else's address. The
first "Physical Address" line is always the adapter's own Driver Info
block, printed before any other device's System Information block.

`parse_topology()` is new for v2 (v1 never needed a full topology parse —
CEC_STANDBY_TARGETS was hardcoded by hand). Treat its exact field-matching
as best-effort pending verification against real `cec-ctl -S` output
during the wizard's dogfooding pass — don't take its shape as gospel the
way the physical-address parsing above can be.
"""
from __future__ import annotations

import asyncio
import glob
import re
import time
from dataclasses import dataclass
from pathlib import Path

from ..health import Health, default_state_dir


@dataclass
class TopologyDevice:
    logical_address: int
    device_type: str
    phys_addr: str | None = None
    osd_name: str | None = None


def discover_adapters() -> list[str]:
    return sorted(glob.glob("/dev/cec*"))


def discover_unattached_pulse8_dongles() -> list[str]:
    """Scan for a Pulse-Eight-protocol USB-CEC dongle that's plugged in but
    not yet bound to /dev/cec*. Generalizes v1's
    udev/pulse8-cec-autoattach.rules (scoped to Alex's exact dongle) to any
    compatible device by udev ID_SERIAL match, covering the many
    Pulse-Eight-protocol clones too — see plans/joystick-notify-v2.md's CEC
    detection strategy, step 2.
    """
    candidates = sorted(glob.glob("/dev/serial/by-id/*Pulse-Eight*"))
    if candidates:
        return candidates
    # Fall back to any unclassified ttyACM node — offering it to the wizard
    # as "maybe a CEC adapter, try attaching?" rather than silently missing
    # it. Genuinely best-effort: confirm the match still holds during
    # dogfooding rather than trusting this heuristic blind.
    return sorted(glob.glob("/dev/ttyACM*"))


def parse_own_physical_address(cec_ctl_dash_s_output: str) -> str | None:
    for raw_line in cec_ctl_dash_s_output.splitlines():
        line = raw_line.strip()
        if not line.startswith("Physical Address"):
            continue
        if ":" not in line:
            # The Capabilities block also lists "Physical Address" as a bare
            # capability-name bullet with no value — must not match that.
            continue
        value = line.split(":", 1)[1].strip()
        if value:
            return value.split()[0]
    return None


_DEVICE_HEADER_RE = re.compile(r"System Information for device (\d+)\s*\(([^)]+)\)", re.IGNORECASE)
_PHYS_ADDR_RE = re.compile(r"Physical Address\s*:\s*(\S+)")
_OSD_NAME_RE = re.compile(r"OSD Name\s*:\s*(.+)")


def parse_topology(cec_ctl_dash_s_output: str) -> list[TopologyDevice]:
    """Best-effort parse of the per-device blocks in `cec-ctl -S`'s
    topology dump — see module docstring for the verification caveat."""
    lines = cec_ctl_dash_s_output.splitlines()
    devices: list[TopologyDevice] = []
    current: TopologyDevice | None = None

    for line in lines:
        header = _DEVICE_HEADER_RE.search(line)
        if header:
            if current is not None:
                devices.append(current)
            current = TopologyDevice(logical_address=int(header.group(1)), device_type=header.group(2).strip())
            continue
        if current is None:
            continue
        phys = _PHYS_ADDR_RE.search(line)
        if phys:
            current.phys_addr = phys.group(1)
            continue
        osd = _OSD_NAME_RE.search(line)
        if osd:
            current.osd_name = osd.group(1).strip()

    if current is not None:
        devices.append(current)
    return devices


def _selfheal_state_path() -> Path:
    return default_state_dir() / "cec-selfheal-last"


async def ensure_adapter(health: Health, *, cooldown_s: float = 120.0) -> str | None:
    """Synchronous self-heal, ported from v1's cec_ensure_adapter_best_effort
    (lib/cec-control.sh): if /dev/cec* is missing right when a wake/standby
    is needed, reattach via the root-owned cec-watchdog.sh
    (packaging/bin/cec-watchdog.sh, triggered here through the sudoers
    NOPASSWD carve-out) rather than waiting for its periodic timer.
    Rate-limited via a state file mtime so repeated controller reconnects
    (or a human actively debugging the adapter) don't each trigger a fresh
    reattach/USB-bus-reset escalation.
    """
    adapters = discover_adapters()
    if adapters:
        health.ok("cec", "adapter present")
        return adapters[0]

    state_path = _selfheal_state_path()
    now = time.time()
    try:
        last = state_path.stat().st_mtime
    except OSError:
        last = 0.0
    if now - last < cooldown_s:
        health.failed("cec", "adapter missing, self-heal on cooldown", f"retry after {cooldown_s - (now - last):.0f}s")
        return None

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.touch()

    try:
        # Absolute path required: the sudoers NOPASSWD carve-out
        # (packaging/joystick-notify.sudoers) matches on the exact command
        # path, not whatever "cec-watchdog.sh" resolves to via $PATH.
        proc = await asyncio.create_subprocess_exec(
            "sudo", "-n", "/usr/lib/joystick-notify/cec-watchdog.sh",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
    except FileNotFoundError:
        health.failed("cec", "self-heal failed", "cec-watchdog.sh not found")
        return None

    for _ in range(5):
        await asyncio.sleep(1)
        adapters = discover_adapters()
        if adapters:
            health.ok("cec", "adapter recovered via self-heal")
            return adapters[0]

    health.failed("cec", "self-heal failed, adapter still missing")
    return None


def find_audio_system_target(topology: list[TopologyDevice]) -> TopologyDevice | None:
    """Auto-populate a receiver/AVR standby target from the topology dump —
    generalizes v1's hardcoded CEC_STANDBY_TARGETS="0 5" into something
    discovered per install."""
    for device in topology:
        if "audio" in device.device_type.lower():
            return device
    return None
