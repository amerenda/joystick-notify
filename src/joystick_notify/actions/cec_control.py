"""HDMI-CEC wake/standby orchestration via cec-ctl subprocess calls — ports
lib/cec-control.sh's logic, with the retry loop's cancellation done via a
real asyncio.Task instead of v1's LAST_MODE_FILE polling trick.

v1's cec_wake_and_select_input_best_effort fired its Active-Source
re-assert retries as a detached `&` background job that had to
cooperatively check a file on every iteration to know whether teardown had
already happened (Pattern A from the audit — the exact bug that could
re-wake the TV with no display behind it, fixed 2026-08-19). Here,
`wake_and_select_input()` returns the `asyncio.Task` running that retry
loop; the caller (state_machine's couch->desk transition) cancels it
directly. Cancellation becomes a language feature, not a file both sides
have to remember to check.
"""
from __future__ import annotations

import asyncio
import logging
import re

from ..health import Health
from ..supervisor import supervise

logger = logging.getLogger(__name__)

CEC_CTL_TIMEOUT_S = 5.0


def _adapter_args(adapter: str | None) -> list[str]:
    return ["-d", adapter] if adapter else []


async def _run(cmd: list[str], timeout: float = CEC_CTL_TIMEOUT_S) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
    except FileNotFoundError:
        return -1, "cec-ctl not found"
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, "timeout"
    return proc.returncode, out.decode(errors="replace")


async def image_view_on(adapter: str | None) -> None:
    # cec-ctl has no separate "power-on" opcode; Image View On (One Touch
    # Play) is the standard wake command and is sufficient on its own
    # (confirmed against real hardware 2026-08-16 in v1: TV replied
    # REPORT_POWER_STATUS pwr-state=to-on after --image-view-on alone).
    await _run(["cec-ctl", *_adapter_args(adapter), "--to", "0", "--image-view-on"])


async def set_stream_path_and_active_source(adapter: str | None, phys_addr: str) -> None:
    args = _adapter_args(adapter)
    await _run(["cec-ctl", *args, "--to", "0", "--set-stream-path", f"phys-addr={phys_addr}"])
    await _run(["cec-ctl", *args, "--to", "0", "--active-source", f"phys-addr={phys_addr}"])


async def wake_and_select_input(
    adapter: str | None,
    phys_addr: str | None,
    health: Health,
    *,
    wake_delay_s: float = 0.0,
    retries: int = 2,
    retry_delay_s: float = 4.0,
) -> asyncio.Task | None:
    """Sends the initial wake + input select synchronously, then spawns and
    returns a Task running the re-assert retry loop (reclaims input from
    competing CEC devices, e.g. an Nvidia Shield waking and sending its own
    Active Source on cold start). The caller owns cancelling this Task on
    teardown — see state_machine.py's `_transition()`, which cancels the
    "owner_watch" task on every transition; daemon.py wires this the same
    way for the CEC retry task specifically.
    """
    await image_view_on(adapter)
    if wake_delay_s > 0:
        await asyncio.sleep(wake_delay_s)
    if not phys_addr:
        logger.info("cec: input switch skipped (no physical address configured)")
        return None
    await set_stream_path_and_active_source(adapter, phys_addr)

    if retries <= 0:
        return None

    async def _retry_loop() -> None:
        try:
            for attempt in range(1, retries + 1):
                await asyncio.sleep(retry_delay_s)
                logger.info("cec: active-source re-assert (retry %d/%d) phys-addr=%s", attempt, retries, phys_addr)
                await set_stream_path_and_active_source(adapter, phys_addr)
        except asyncio.CancelledError:
            return

    return supervise("cec_retry_loop", _retry_loop(), health)


_PWR_STATE_RE = re.compile(r"pwr-state\s*:\s*([a-zA-Z-]+)", re.IGNORECASE)


def parse_power_status(output: str) -> str:
    match = _PWR_STATE_RE.search(output)
    if not match:
        return "unknown"
    state = match.group(1).lower()
    if "standby" in state:
        return "standby"
    if state in ("on", "to-on"):
        return "on"
    return "unknown"


async def power_status(adapter: str | None, logical_addr: int) -> str:
    rc, out = await _run(["cec-ctl", *_adapter_args(adapter), "--to", str(logical_addr), "--give-device-power-status"])
    if rc != 0:
        return "unknown"
    return parse_power_status(out)


async def standby_and_verify(
    adapter: str | None,
    targets: list[int],
    health: Health,
    *,
    attempts: int = 3,
    delay_s: float = 2.0,
) -> list[int]:
    """Send Standby to every target and confirm each one actually reports
    standby before returning — a fire-and-forget standby command can
    silently no-op (v1's rationale, unchanged: a receiver asleep-but-not-
    really, a dropped CEC frame, a device that ignores broadcast standby).
    Returns the list of addresses that never confirmed; reports Health
    degraded (not failed — teardown still completes) so it's visible
    without blocking the mode switch.
    """
    unconfirmed: list[int] = []
    for addr in targets:
        status = "unknown"
        for attempt in range(1, attempts + 1):
            await _run(["cec-ctl", *_adapter_args(adapter), "--to", str(addr), "--standby"])
            await asyncio.sleep(delay_s)
            status = await power_status(adapter, addr)
            if status == "standby":
                logger.info("cec: standby confirmed for logical addr %d (attempt %d/%d)", addr, attempt, attempts)
                break
            logger.info("cec: standby not yet confirmed for logical addr %d (attempt %d/%d, status=%s)", addr, attempt, attempts, status)
        if status != "standby":
            unconfirmed.append(addr)

    if unconfirmed:
        # The per-attempt lines above are only ever INFO -- without this,
        # the final degraded outcome (the thing anyone troubleshooting
        # "TV didn't turn off" actually needs to see) had no log line of
        # its own at all, only a Health.degraded() call with no matching
        # entry in the event log.
        logger.warning("cec: standby unconfirmed for address(es) %s, device(s) may still be on", unconfirmed)
        health.degraded("cec", f"standby unconfirmed for address(es) {unconfirmed}", "device(s) may still be on")
    else:
        health.ok("cec", "standby confirmed for all targets")
    return unconfirmed
