"""kscreen-doctor wrapper with output-applied verification — ports
lib/display-control.sh. v1's 2026-07-30 incident (a switch logged as
"completed" but the output never actually enabled at the compositor level)
is why every apply here is followed by re-reading `kscreen-doctor -j` and
checking the *reported* enabled state, not just the subprocess exit code.

JSON parsing and DRM connector-status reading are pure/injectable
functions and are unit-tested directly (see tests/test_display.py).
Subprocess orchestration (the retry loops, `couch_mode`/`desk_mode`
activation) is real I/O, exercised only on a box with actual outputs —
consistent with "don't test anything live" for this build pass.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from ..config.schema import DisplayConfig
from ..health import Health
from ..state_machine import ActivationError, Mode

logger = logging.getLogger(__name__)

KSCREEN_TIMEOUT_S = 10.0


@dataclass
class OutputInfo:
    name: str
    enabled: bool
    connected: bool
    model: str = ""
    preferred_mode: str = ""


def _mode_label(mode: dict) -> str:
    # Real kscreen-doctor -j output already provides a clean, pre-formatted
    # "name" per mode (e.g. "2560x1440@60") — use it directly rather than
    # reconstructing from raw width/height/refreshRate, which produces ugly
    # float-precision artifacts (refreshRate is often something like
    # 59.95100021362305, not a clean "60") and risks not matching the exact
    # string kscreen-doctor itself expects for `output.X.mode.<name>`.
    name = mode.get("name")
    if name:
        return name
    size = mode.get("size", {})
    refresh = mode.get("refreshRate", "")
    return f"{size.get('width', '?')}x{size.get('height', '?')}@{refresh}"


def parse_outputs(kscreen_json: dict) -> list[OutputInfo]:
    outputs = []
    for o in kscreen_json.get("outputs", []):
        # Real kscreen-doctor -j output (confirmed via live testing
        # 2026-08-21, KDE Plasma on archlinux) uses a `preferredModes`
        # list of mode ids, not the singular `preferredModeId` or a
        # per-mode `preferred` boolean this was originally written
        # against — neither of those fields were ever populated on real
        # data. Falls back to the currently-active mode (currentModeId)
        # when no preferred-mode info is available at all, so the wizard
        # still has a sane default to pre-fill rather than a blank field.
        preferred_ids = set(o.get("preferredModes") or [])
        preferred = ""
        current_mode_label = ""
        for mode in o.get("modes", []):
            if mode.get("id") == o.get("currentModeId"):
                current_mode_label = _mode_label(mode)
            if mode.get("id") in preferred_ids or mode.get("id") == o.get("preferredModeId") or mode.get("preferred"):
                preferred = _mode_label(mode)
        if not preferred:
            preferred = current_mode_label
        outputs.append(
            OutputInfo(
                name=o.get("name", ""),
                enabled=bool(o.get("enabled")),
                connected=bool(o.get("connected", True)),
                model=o.get("edid", {}).get("name", "") if isinstance(o.get("edid"), dict) else "",
                preferred_mode=preferred,
            )
        )
    return outputs


def output_enabled(kscreen_json: dict, port: str) -> bool:
    for o in kscreen_json.get("outputs", []):
        if o.get("name") == port:
            return bool(o.get("enabled"))
    return False


def connector_status(port: str, sysfs_root: Path | str = "/sys/class/drm") -> str:
    """Reads DRM connector status for the given port (e.g. HDMI-A-1).
    Returns "connected", "disconnected", or "unknown". `sysfs_root` is
    injectable so this is unit-testable against a fake directory tree.
    """
    root = Path(sysfs_root)
    if not root.exists():
        return "unknown"
    for card_dir in sorted(root.glob(f"card*-{port}")):
        status_file = card_dir / "status"
        if status_file.exists():
            try:
                return status_file.read_text().strip()
            except OSError:
                return "unknown"
    return "unknown"


async def _run(cmd: list[str], timeout: float = KSCREEN_TIMEOUT_S) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
    except FileNotFoundError as e:
        return -1, str(e)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, "timeout"
    return proc.returncode, out.decode(errors="replace")


async def get_kscreen_json() -> dict | None:
    rc, out = await _run(["kscreen-doctor", "-j"])
    if rc != 0:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


async def trigger_connector_rescan(port: str, sysfs_root: Path | str = "/sys/class/drm") -> None:
    """Ask the kernel to re-probe the connector via udev — helps when a
    receiver is slow to present EDID after CEC wakes it. Requires the root
    carve-out (packaging/systemd, narrowly-scoped sudoers/polkit rule for
    this one operation) — see plans/joystick-notify-v2.md decision #3.
    """
    root = Path(sysfs_root)
    for card_dir in sorted(root.glob(f"card*-{port}")):
        rc, out = await _run(["sudo", "-n", "udevadm", "trigger", "--action=change", str(card_dir)])
        if rc == 0:
            logger.info("display: triggered DRM rescan for %s", port)
        else:
            logger.warning("display: DRM rescan failed for %s (missing sudoers/polkit rule?): %s", port, out)
        return


async def _apply_and_verify(enable_port: str, mode: str, disable_port: str, *, max_attempts: int, retry_delay_s: float) -> bool:
    for attempt in range(1, max_attempts + 1):
        await asyncio.sleep(0.5)  # let GPU/driver settle before display changes (AMD RDNA3 workaround, carried from v1)
        rc, out = await _run(
            [
                "kscreen-doctor",
                f"output.{enable_port}.enable",
                f"output.{enable_port}.priority.1",
                f"output.{enable_port}.mode.{mode}",
                f"output.{enable_port}.position.0,0",
                f"output.{disable_port}.disable",
            ]
        )
        kjson = await get_kscreen_json()
        if rc == 0 and kjson is not None and output_enabled(kjson, enable_port):
            logger.info("display: %s active (attempt %d/%d)", enable_port, attempt, max_attempts)
            return True
        logger.warning("display: switch to %s did not take (attempt %d/%d, rc=%d): %s", enable_port, attempt, max_attempts, rc, out)
        if attempt < max_attempts:
            await asyncio.sleep(retry_delay_s)
    return False


async def detect_active_mode(config: DisplayConfig) -> Mode | None:
    """Startup reconciliation helper (see
    state_machine.StateMachine.reconcile_startup_mode): reads live
    kscreen-doctor output to determine which of the two configured ports is
    actually enabled right now, independent of whatever state_machine.mode
    defaulted to at construction. This is the fix for the 2026-08-31
    incident where a daemon restart mid-couch-session left state_machine.mode
    at its hardcoded DESK default while the TV was still plainly live and
    the desk monitor was black.

    Returns None when live state is inconclusive (kscreen-doctor
    unreachable, or the two ports don't disagree -- neither or both
    reporting enabled) so the caller knows not to trust a guess rather than
    picking one arbitrarily.
    """
    kjson = await get_kscreen_json()
    if kjson is None:
        return None
    couch_enabled = output_enabled(kjson, config.couch_port)
    desk_enabled = output_enabled(kjson, config.desk_port)
    if couch_enabled and not desk_enabled:
        return Mode.COUCH
    if desk_enabled and not couch_enabled:
        return Mode.DESK
    return None


async def activate_desk(config: DisplayConfig, health: Health) -> None:
    ok = await _apply_and_verify(config.desk_port, config.desk_mode, config.couch_port, max_attempts=5, retry_delay_s=1.0)
    if ok:
        health.ok("display", f"desk output {config.desk_port} active")
    else:
        health.failed("display", f"failed to switch to desk output {config.desk_port}")


async def activate_couch(config: DisplayConfig, health: Health) -> None:
    """Raises ActivationError if the couch output never comes up — the
    caller (state_machine) catches this and falls back to desk mode rather
    than leaving the system half-switched, matching v1's explicit
    "Returning to desk mode" behavior in display-control.sh.
    """
    logger.info("display: waiting 5s for receiver to respond after CEC wake...")
    await asyncio.sleep(5)
    await trigger_connector_rescan(config.couch_port)

    max_attempts, retry_delay_s = 15, 2.0
    connected = False
    for attempt in range(1, max_attempts + 1):
        status = connector_status(config.couch_port)
        if status == "connected":
            logger.info("display: %s connected (attempt %d/%d)", config.couch_port, attempt, max_attempts)
            connected = True
            break
        if attempt < max_attempts:
            await trigger_connector_rescan(config.couch_port)
            logger.warning("display: %s is %s (attempt %d/%d), waiting for receiver EDID", config.couch_port, status, attempt, max_attempts)
            await asyncio.sleep(retry_delay_s)

    if not connected:
        raise ActivationError(
            "display",
            f"couch output {config.couch_port} never reported connected",
            "TV/receiver may not be responding to CEC or presenting EDID",
        )

    ok = await _apply_and_verify(config.couch_port, config.couch_mode, config.desk_port, max_attempts=5, retry_delay_s=1.0)
    if not ok:
        raise ActivationError("display", f"kscreen-doctor failed to switch to couch output {config.couch_port}")

    health.ok("display", f"couch output {config.couch_port} active")
