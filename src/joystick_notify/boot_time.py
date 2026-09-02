"""Shared fresh-boot detection.

A full OS reboot has no possible live session to protect or preserve --
nothing survives a reboot. Multiple independent subsystems need exactly
this signal to tell "the whole machine just started" apart from "this
daemon process just (re)started on an already-running system":

- `activity_gate.py` (2026-08-29): a controller already connected at
  daemon startup is trusted immediately on a fresh boot, but held
  indefinitely otherwise (stale-carryover protection from a genuine
  prior session).
- `state_machine.py` (2026-09-02): live display hardware left
  couch-configured is trusted as a real, intentional session on a daemon
  restart, but actively corrected back to desk on a fresh boot instead
  -- it's just KWin/the GPU driver restoring its last output layout
  across the reboot, not evidence anyone wants couch mode right now.

`DEFAULT_FRESH_BOOT_UPTIME_THRESHOLD_S` (120s) is generous for how long
a normal boot-to-login sequence takes while still being far short of any
realistic "I've been using this machine for a while" duration.
"""
from __future__ import annotations

from typing import Callable

DEFAULT_FRESH_BOOT_UPTIME_THRESHOLD_S = 120.0


def read_system_uptime_s() -> float:
    try:
        with open("/proc/uptime") as f:
            return float(f.readline().split()[0])
    except (OSError, ValueError, IndexError):
        # Unknown uptime -- assume NOT a fresh boot, the conservative
        # default that preserves whatever protection the caller applies
        # to a non-fresh-boot restart, rather than risking a false
        # positive that skips it.
        return float("inf")


def is_fresh_boot(
    threshold_s: float = DEFAULT_FRESH_BOOT_UPTIME_THRESHOLD_S,
    *,
    system_uptime_s: Callable[[], float] = read_system_uptime_s,
) -> bool:
    return system_uptime_s() < threshold_s
