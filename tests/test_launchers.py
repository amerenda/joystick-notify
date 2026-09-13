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


# --- _get_steam_pids: precise match, not the generic substring one ---
# Direct regression test for the 2026-09-12 incident: get_matching_pids's
# broad substring match also matched Steam's own error-dialog helper,
# steam_msg.sh (spawned by Steam itself on a failed launch), silently
# treating a stuck, un-dismissed dialog as "Steam still running" for
# hours. _get_steam_pids() must match only the real client.


def test_get_steam_pids_matches_real_client_by_comm(tmp_path):
    from joystick_notify.actions.launchers import _get_steam_pids

    _make_fake_proc(tmp_path, "123", comm="steam", cmdline="/home/alex/.local/share/Steam/ubuntu12_32/steam\x00-gamepadui\x00")
    assert _get_steam_pids(proc_root=str(tmp_path)) == {"123"}


def test_get_steam_pids_matches_by_cmdline_argv0_basename_when_comm_differs(tmp_path):
    from joystick_notify.actions.launchers import _get_steam_pids

    # Falls back to argv[0]'s basename when comm isn't exactly "steam" --
    # still the real client's own launcher path, not a substring match.
    _make_fake_proc(tmp_path, "123", comm="steam.bin", cmdline="/home/alex/.local/share/Steam/ubuntu12_32/steam\x00-gamepadui\x00")
    assert _get_steam_pids(proc_root=str(tmp_path)) == {"123"}


def test_get_steam_pids_excludes_steam_msg_dialog_helper(tmp_path):
    from joystick_notify.actions.launchers import _get_steam_pids

    # The exact shape of the 2026-09-12 incident: Steam's own
    # "Unable to open a connection to X" error dialog, left stuck for
    # hours, previously counted as "Steam still running."
    _make_fake_proc(
        tmp_path, "456", comm="steam_msg.sh",
        cmdline="bash\x00/home/alex/.local/share/Steam/steam_msg.sh\x00--title\x00Unable to open a connection to X\x00",
    )
    assert _get_steam_pids(proc_root=str(tmp_path)) == set()


def test_get_steam_pids_excludes_runtime_helper_processes(tmp_path):
    from joystick_notify.actions.launchers import _get_steam_pids

    # steam-runtime-launcher-service, srt-logger, steamwebhelper, etc. --
    # all real child processes of a genuine Steam session, none of which
    # is the client itself and none of which -shutdown targets.
    _make_fake_proc(tmp_path, "789", comm="steam-runtime-l", cmdline="steam-runtime-launcher-service\x00--alongside-steam\x00")
    _make_fake_proc(tmp_path, "790", comm="srt-logger", cmdline="/home/alex/.local/share/Steam/ubuntu12_32/steam-runtime/usr/libexec/steam-runtime-tools-0/srt-logger\x00")
    assert _get_steam_pids(proc_root=str(tmp_path)) == set()


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


# --- launch_steam_bigpicture / exit_launched ---
# Direct regression tests for the real 2026-08-22 bug: switching to couch
# mode while Big Picture was already running (left over from a previous
# session) reused it via the old `-ifrunning` deep link *after* the couch
# display's resolution switch had already happened underneath its still-
# live window -- it briefly showed Big Picture in a floating window, then
# the output lost signal entirely. Fixed by always fully shutting Steam
# down first and cold-starting fresh, which can't inherit a stale
# swapchain from before a mode switch.


@pytest.mark.asyncio
async def test_launch_steam_bigpicture_cold_starts_directly_when_not_running(monkeypatch):
    from joystick_notify.actions import launchers

    calls = []

    async def fake_run_detached(cmd):
        calls.append(cmd)
        return None

    monkeypatch.setattr(launchers, "_run_detached", fake_run_detached)
    monkeypatch.setattr(launchers, "_is_steam_running", lambda: False)

    await launchers.launch_steam_bigpicture()

    assert calls == [["steam", "-gamepadui"]]


@pytest.mark.asyncio
async def test_launch_steam_bigpicture_shuts_down_before_cold_starting_when_already_running(monkeypatch):
    from joystick_notify.actions import launchers

    calls = []

    async def fake_run_detached(cmd):
        calls.append(cmd)
        return None

    monkeypatch.setattr(launchers, "_run_detached", fake_run_detached)
    monkeypatch.setattr(launchers, "_is_steam_running", lambda: True)  # outer check: shutdown needed

    seen = {"n": 0}

    def fake_get_steam_pids():
        seen["n"] += 1
        return {"111"} if seen["n"] == 1 else set()  # prior pid, then confirmed gone

    monkeypatch.setattr(launchers, "_get_steam_pids", fake_get_steam_pids)

    await launchers.launch_steam_bigpicture()

    assert calls == [["steam", "-shutdown"], ["steam", "-gamepadui"]]


@pytest.mark.asyncio
async def test_shutdown_steam_and_wait_gives_up_after_timeout_and_logs_warning(monkeypatch, caplog):
    from joystick_notify.actions import launchers

    async def fake_run_detached(cmd):
        return None

    monkeypatch.setattr(launchers, "_run_detached", fake_run_detached)
    monkeypatch.setattr(launchers, "_get_steam_pids", lambda: {"111"})  # same prior pid, never exits

    with caplog.at_level("WARNING", logger="joystick_notify.actions.launchers"):
        await launchers._shutdown_steam_and_wait(poll_s=0, timeout_s=0.02)

    assert any("still running" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_shutdown_steam_and_wait_ignores_shutdowns_own_bootstrap_process(monkeypatch, caplog):
    # Regression test for the 2026-09-07 live race (see docstring on
    # _shutdown_steam_and_wait): `steam -shutdown` spawns its own
    # transient bootstrap process that still matches "steam" in /proc.
    # The prior PID (111) disappears quickly, but a *different* PID (999)
    # -- the bootstrap process `-shutdown` itself just spawned -- is still
    # alive for the rest of the timeout window. The wait must complete as
    # soon as the prior PID is gone, and must NOT treat the new PID as
    # "steam still running" and time out.
    from joystick_notify.actions import launchers

    async def fake_run_detached(cmd):
        return None

    monkeypatch.setattr(launchers, "_run_detached", fake_run_detached)

    calls = {"n": 0}

    def fake_get_steam_pids():
        calls["n"] += 1
        if calls["n"] == 1:
            return {"111"}  # captured as prior_pids before -shutdown fires
        return {"999"}  # -shutdown's own bootstrap process, unrelated pid

    monkeypatch.setattr(launchers, "_get_steam_pids", fake_get_steam_pids)

    with caplog.at_level("WARNING", logger="joystick_notify.actions.launchers"):
        await launchers._shutdown_steam_and_wait(poll_s=0, timeout_s=0.05)

    assert not any("still running" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_exit_launched_shuts_down_steam_when_running(monkeypatch):
    from joystick_notify.actions import launchers

    calls = []

    async def fake_run_detached(cmd):
        calls.append(cmd)
        return None

    monkeypatch.setattr(launchers, "_run_detached", fake_run_detached)
    monkeypatch.setattr(launchers, "_is_steam_running", lambda: True)  # outer check: shutdown needed
    monkeypatch.setattr(launchers, "_get_steam_pids", lambda: set())  # confirmed gone immediately

    await launchers.exit_launched("steam-bigpicture")

    assert calls == [["steam", "-shutdown"]]


@pytest.mark.asyncio
async def test_exit_launched_noop_when_steam_not_running(monkeypatch):
    from joystick_notify.actions import launchers

    calls = []

    async def fake_run_detached(cmd):
        calls.append(cmd)
        return None

    monkeypatch.setattr(launchers, "_run_detached", fake_run_detached)
    monkeypatch.setattr(launchers, "_is_steam_running", lambda: False)

    await launchers.exit_launched("steam-bigpicture")

    assert calls == []


@pytest.mark.asyncio
async def test_exit_launched_custom_command_with_no_teardown_command_does_nothing(monkeypatch):
    # A custom launch command with no teardown_command configured is a
    # valid, explicit choice (leave it running across desk<->couch), not
    # a gap to warn about -- Sunshine-style paired commands, full user
    # control, no hardcoded assumption about what "graceful exit" means
    # for an arbitrary command.
    from joystick_notify.actions import launchers

    calls = []

    async def fake_run_detached(cmd):
        calls.append(cmd)
        return None

    monkeypatch.setattr(launchers, "_run_detached", fake_run_detached)

    await launchers.exit_launched("my-custom-command --flag")

    assert calls == []


@pytest.mark.asyncio
async def test_exit_launched_uses_custom_teardown_command_when_set(monkeypatch):
    from joystick_notify.actions import launchers

    calls = []

    async def fake_run_detached(cmd):
        calls.append(cmd)
        return None

    monkeypatch.setattr(launchers, "_run_detached", fake_run_detached)

    await launchers.exit_launched("my-custom-command --flag", "my-custom-command --quit")

    assert calls == [["/bin/sh", "-c", "my-custom-command --quit"]]


@pytest.mark.asyncio
async def test_exit_launched_teardown_command_overrides_steam_bigpicture_default(monkeypatch):
    # Any non-empty teardown_command always wins, even for the
    # steam-bigpicture preset -- full user override, not just a fallback
    # for unrecognized commands.
    from joystick_notify.actions import launchers

    calls = []

    async def fake_run_detached(cmd):
        calls.append(cmd)
        return None

    monkeypatch.setattr(launchers, "_run_detached", fake_run_detached)
    monkeypatch.setattr(launchers, "_is_steam_running", lambda: True)

    await launchers.exit_launched("steam-bigpicture", "my-custom-teardown.sh")

    assert calls == [["/bin/sh", "-c", "my-custom-teardown.sh"]]


@pytest.mark.asyncio
async def test_exit_launched_empty_everything_does_nothing(monkeypatch):
    from joystick_notify.actions import launchers

    calls = []

    async def fake_run_detached(cmd):
        calls.append(cmd)
        return None

    monkeypatch.setattr(launchers, "_run_detached", fake_run_detached)

    await launchers.exit_launched("")

    assert calls == []


@pytest.mark.asyncio
async def test_shutdown_steam_and_wait_logs_pid_diagnostics_on_timeout(monkeypatch, caplog):
    # Regression test for the 2026-09-07 live retest: the PID-tracking fix
    # (prior_pids) was correct, but a live retest still raced because the
    # real prior-session teardown took longer than the old 10s timeout on
    # archlinux's hardware -- with no way to tell that apart from a
    # tracking-logic bug other than re-diagnosing from Steam's own logs.
    # This locks in that a timeout now logs the exact PID(s) it was still
    # waiting on, not just a generic "still running" message.
    from joystick_notify.actions import launchers

    async def fake_run_detached(cmd):
        return None

    monkeypatch.setattr(launchers, "_run_detached", fake_run_detached)
    monkeypatch.setattr(launchers, "_get_steam_pids", lambda: {"111", "222"})

    with caplog.at_level("DEBUG", logger="joystick_notify.actions.launchers"):
        await launchers._shutdown_steam_and_wait(poll_s=0, timeout_s=0.02)

    assert any("prior steam pids before -shutdown" in r.message and "111" in r.message for r in caplog.records)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("111" in r.message and "222" in r.message for r in warnings)
