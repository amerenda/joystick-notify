"""Covers the "Tray icon states — what counts as broken" design in
plans/joystick-notify-v2.md: OK/degraded/failed aggregation, cross-process
persistence, and the heartbeat-based daemon-liveness check that stops the
tray from showing a stale "OK" after the daemon has actually crashed.
"""
import time
from pathlib import Path

import pytest

from joystick_notify.health import Health, Status, read_snapshot


def test_ok_only_components_yield_overall_ok(tmp_path):
    h = Health(path=Path(tmp_path) / "health.json")
    h.ok("devices")
    h.ok("display")
    assert h.overall() == Status.OK


def test_idle_no_controller_is_ok_not_failed(tmp_path):
    """The explicit distinction the plan calls out: 'no controller currently
    connected' is a normal idle state, not a broken detection subsystem."""
    h = Health(path=Path(tmp_path) / "health.json")
    h.ok("devices", reason="no controller connected (idle)")
    assert h.overall() == Status.OK
    assert h.get("devices").status == Status.OK


def test_broken_detection_subsystem_is_failed(tmp_path):
    h = Health(path=Path(tmp_path) / "health.json")
    h.failed("devices", "udev rules not installed", detail="permission denied on /dev/input")
    assert h.overall() == Status.FAILED


def test_degraded_without_any_failed_yields_degraded_overall(tmp_path):
    h = Health(path=Path(tmp_path) / "health.json")
    h.ok("devices")
    h.degraded("cec", "target found but not yet confirmed")
    assert h.overall() == Status.DEGRADED


def test_failed_takes_precedence_over_degraded(tmp_path):
    h = Health(path=Path(tmp_path) / "health.json")
    h.degraded("cec", "unconfirmed")
    h.failed("display", "couch output not found")
    assert h.overall() == Status.FAILED


def test_persisted_snapshot_readable_from_separate_process_view(tmp_path):
    path = Path(tmp_path) / "health.json"
    h = Health(path=path)
    h.failed("deps", "cec-ctl not found")

    snap = read_snapshot(path)
    assert snap is not None
    assert snap.daemon_alive is True
    assert snap.overall == Status.FAILED
    assert snap.components["deps"].reason == "cec-ctl not found"


def test_missing_snapshot_file_returns_none_not_error(tmp_path):
    path = Path(tmp_path) / "does-not-exist.json"
    assert read_snapshot(path) is None


def test_stale_heartbeat_means_daemon_not_alive(tmp_path):
    """Direct test of the gap flagged during planning: without this check,
    a crashed daemon would leave behind a last-good 'OK' snapshot that the
    tray could misread as still-running."""
    path = Path(tmp_path) / "health.json"
    h = Health(path=path)
    h.ok("devices")

    # Simulate a stale heartbeat by rewriting the persisted timestamp directly
    # (this is what an actually-crashed daemon's last snapshot looks like).
    import json

    with open(path) as f:
        raw = json.load(f)
    raw["heartbeat"] = time.time() - 3600
    with open(path, "w") as f:
        json.dump(raw, f)

    snap = read_snapshot(path)
    assert snap is not None
    assert snap.daemon_alive is False
    with pytest.raises(RuntimeError):
        _ = snap.overall


def test_heartbeat_without_status_change_refreshes_liveness(tmp_path):
    path = Path(tmp_path) / "health.json"
    h = Health(path=path)
    h.ok("devices")
    first = read_snapshot(path).heartbeat

    time.sleep(0.01)
    h.heartbeat()
    second = read_snapshot(path).heartbeat

    assert second > first
