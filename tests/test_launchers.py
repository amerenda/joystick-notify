import asyncio
import logging
from pathlib import Path

import pytest

from joystick_notify.actions.launchers import _run_detached, detect_launchers, is_process_running


def test_detect_launchers_finds_steam(tmp_path):
    (Path(tmp_path) / ".steam").mkdir()
    result = detect_launchers(home=Path(tmp_path))
    assert result["steam"] is True
    assert result["lutris"] is False


def test_detect_launchers_finds_flatpak_heroic(tmp_path):
    (Path(tmp_path) / ".var/app/com.heroicgameslauncher.hgl").mkdir(parents=True)
    result = detect_launchers(home=Path(tmp_path))
    assert result["heroic"] is True


def test_detect_launchers_none_installed(tmp_path):
    result = detect_launchers(home=Path(tmp_path))
    assert all(v is False for v in result.values())


def _make_fake_proc(tmp_path, pid: str, comm: str = "", cmdline: str = ""):
    proc_dir = Path(tmp_path) / pid
    proc_dir.mkdir()
    (proc_dir / "comm").write_text(comm + "\n")
    (proc_dir / "cmdline").write_bytes(cmdline.encode())


def test_is_process_running_matches_comm(tmp_path):
    _make_fake_proc(tmp_path, "123", comm="steam")
    assert is_process_running(["steam"], proc_root=str(tmp_path)) is True


def test_is_process_running_matches_cmdline_when_comm_truncated(tmp_path):
    # Real-world case: /proc/<pid>/comm truncates to 15 chars, so long
    # binary paths only show up fully in cmdline.
    _make_fake_proc(tmp_path, "456", comm="gamescope", cmdline="/usr/bin/gamescope\x00--fullscreen\x00")
    assert is_process_running(["gamescope"], proc_root=str(tmp_path)) is True


def test_is_process_running_false_when_no_match(tmp_path):
    _make_fake_proc(tmp_path, "789", comm="bash")
    assert is_process_running(["steam"], proc_root=str(tmp_path)) is False


# --- _run_detached error visibility ---
# Direct regression test for the 2026-08-21 live-testing finding: Steam's
# real "unable to open a connection to X" failure was completely
# invisible in the logs because output was discarded to DEVNULL. A
# launch failure must be logged, not just silently swallowed, even though
# the launch itself stays non-blocking for the caller.


@pytest.mark.asyncio
async def test_run_detached_logs_nonzero_exit_with_output(caplog):
    with caplog.at_level(logging.ERROR, logger="joystick_notify.actions.launchers"):
        await _run_detached(["/bin/sh", "-c", "echo 'unable to open a connection to X' >&2; exit 1"])
        await asyncio.sleep(0.1)  # let the background _log_outcome task run

    assert any("exited 1" in r.message and "unable to open a connection to X" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_run_detached_does_not_log_error_on_success(caplog):
    with caplog.at_level(logging.ERROR, logger="joystick_notify.actions.launchers"):
        await _run_detached(["/bin/sh", "-c", "exit 0"])
        await asyncio.sleep(0.1)

    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


@pytest.mark.asyncio
async def test_run_detached_does_not_block_on_long_running_command():
    # The caller must not be blocked waiting for the process to finish --
    # that's the entire "detached" contract this helper exists for.
    start = asyncio.get_event_loop().time()
    await _run_detached(["/bin/sh", "-c", "sleep 2"])
    elapsed = asyncio.get_event_loop().time() - start
    assert elapsed < 1.0
