import time

from joystick_notify.health import ComponentStatus, HealthSnapshot, Status
from joystick_notify.tray.tray import TrayState, tray_state


def _snapshot(components: dict, heartbeat: float | None = None) -> HealthSnapshot:
    return HealthSnapshot(heartbeat=heartbeat if heartbeat is not None else time.time(), components=components)


def test_no_snapshot_is_daemon_unreachable():
    assert tray_state(None, configured=True) == TrayState.DAEMON_UNREACHABLE


def test_stale_heartbeat_is_daemon_unreachable_even_if_all_ok():
    stale = _snapshot({"devices": ComponentStatus(Status.OK)}, heartbeat=time.time() - 3600)
    assert tray_state(stale, configured=True) == TrayState.DAEMON_UNREACHABLE


def test_unconfigured_takes_priority_when_daemon_alive():
    snap = _snapshot({})
    assert tray_state(snap, configured=False) == TrayState.UNCONFIGURED


def test_all_ok_and_configured_is_ok():
    snap = _snapshot({"devices": ComponentStatus(Status.OK), "display": ComponentStatus(Status.OK)})
    assert tray_state(snap, configured=True) == TrayState.OK


def test_idle_no_controller_still_ok_not_broken():
    # Direct regression check for the plan's explicit distinction: "no
    # controller connected" reports Status.OK (see health.py docstring),
    # so the tray must not show broken just because nothing is plugged in.
    snap = _snapshot({"devices": ComponentStatus(Status.OK, reason="no controller connected (idle)")})
    assert tray_state(snap, configured=True) == TrayState.OK


def test_degraded_component_without_failed_is_degraded():
    snap = _snapshot({"cec": ComponentStatus(Status.DEGRADED, reason="target unconfirmed")})
    assert tray_state(snap, configured=True) == TrayState.DEGRADED


def test_failed_component_is_broken():
    snap = _snapshot({"display": ComponentStatus(Status.FAILED, reason="couch output not found")})
    assert tray_state(snap, configured=True) == TrayState.BROKEN


def test_failed_takes_priority_over_degraded():
    snap = _snapshot(
        {
            "cec": ComponentStatus(Status.DEGRADED, reason="unconfirmed"),
            "display": ComponentStatus(Status.FAILED, reason="couch output gone"),
        }
    )
    assert tray_state(snap, configured=True) == TrayState.BROKEN
