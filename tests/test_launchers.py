from pathlib import Path

from joystick_notify.actions.launchers import detect_launchers, is_process_running


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
