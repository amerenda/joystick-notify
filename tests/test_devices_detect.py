import os
from pathlib import Path

from joystick_notify.debounce import RawEvent, RawKind
from joystick_notify.devices.detect import (
    HidrawLivenessWatcher,
    UdevWatcher,
    device_present,
    owner_present,
    _is_status_only_report,
    device_name,
    find_evdev_path_for_device,
    is_candidate_hid,
    parse_hid_id,
    profile_for,
    stable_device_id,
    vendor_product,
)
from joystick_notify.devices.profiles import GENERIC_PROFILE, match_profile
from joystick_notify.health import Health


def test_stable_device_id_prefers_hid_uniq():
    assert stable_device_id({"HID_UNIQ": "e4:17:d8:bb:e0:03"}) == "e4:17:d8:bb:e0:03"


def test_stable_device_id_falls_back_to_usb_vidpid_when_no_uniq():
    # This is exactly v1's 8BitDo Ultimate 2 bug: HID_UNIQ has no colon and
    # was wrongly matched against a Bluetooth-shaped glob. Here it's just
    # not present at all, so we fall back to vid:pid — still a stable id.
    assert stable_device_id({"ID_VENDOR_ID": "2dc8", "ID_MODEL_ID": "310b"}) == "usb:2dc8:310b"


def test_stable_device_id_none_when_nothing_usable():
    assert stable_device_id({}) is None


def test_is_candidate_hid_true_for_joystick_tagged_device():
    assert is_candidate_hid({"ID_INPUT_JOYSTICK": "1"}) is True


def test_is_candidate_hid_true_for_name_pattern_without_joystick_tag():
    assert is_candidate_hid({"HID_NAME": "8BitDo Ultimate 2 Wireless Controller"}) is True


def test_is_candidate_hid_excludes_led_devices():
    assert is_candidate_hid({"HID_NAME": "8BitDo Controller LED"}) is False


def test_is_candidate_hid_false_for_unrelated_device():
    assert is_candidate_hid({"HID_NAME": "Logitech Mouse"}) is False


def test_is_candidate_hid_excludes_virtual_uinput_device_even_with_joystick_tag():
    # Regression test for the 2026-09-12 MoonDeckBuddy incident: a
    # relayed virtual gamepad still carries ID_INPUT_JOYSTICK=1 and would
    # otherwise pass every other check, triggering a full couch-mode
    # activation for what should be an unlock-only remote stream.
    assert is_candidate_hid({
        "DEVPATH": "/devices/virtual/input/input53",
        "ID_INPUT_JOYSTICK": "1",
    }) is False


def test_is_candidate_hid_excludes_virtual_uinput_device_by_valve_vendor_too():
    assert is_candidate_hid({
        "DEVPATH": "/devices/virtual/input/input53",
        "HID_ID": "0003:000028DE:00001102",
        "HID_NAME": "Microsoft X-Box 360 pad",
    }) is False


def test_profile_for_matches_8bitdo_by_vendor_and_name():
    profile = profile_for({"ID_VENDOR_ID": "2dc8", "HID_NAME": "8BitDo Ultimate 2"})
    assert profile.id == "8bitdo"
    assert profile.device_class == "bitdo_dongle"


def test_profile_for_unknown_device_returns_generic_not_error():
    profile = profile_for({"ID_VENDOR_ID": "ffff", "ID_MODEL_ID": "ffff", "HID_NAME": "Mystery Pad"})
    assert profile is GENERIC_PROFILE


def test_match_profile_dualsense():
    profile = match_profile(vendor_id="054C", product_id="0CE6")
    assert profile.id == "dualsense"


def test_match_profile_steam_controller_requires_name_pattern():
    # Valve vendor ID alone (e.g. a non-controller Valve HID device) should
    # not false-positive without the name pattern too.
    assert match_profile(vendor_id="28de", hid_name="Some Other Valve Device").id == "generic"
    assert match_profile(vendor_id="28de", hid_name="Steam Controller").id == "steam_controller"


# --- Regression tests for the 2026-08-20 live-testing findings ---
# Real testing against an actual 8BitDo Ultimate 2 (2.4G dongle) and a real
# Steam Controller Puck receiver on archlinux found that every device
# detected via the hidraw-liveness fallback was silently classified as
# "generic" instead of its real profile, because that path only has
# HID_ID (raw sysfs uevent), never the udev-computed ID_VENDOR_ID/
# ID_MODEL_ID that profile_for() was reading. This is exactly backwards:
# hidraw-liveness is the path used for the bounciest hardware (Puck,
# 8BitDo dongle) that most needs correct per-class debounce timing.


def test_parse_hid_id_extracts_vendor_and_product():
    assert parse_hid_id("0003:00002DC8:00006012") == ("2dc8", "6012")


def test_parse_hid_id_malformed_returns_none():
    assert parse_hid_id("not-a-hid-id") is None
    assert parse_hid_id("") is None


def test_vendor_product_prefers_live_udev_properties():
    props = {"ID_VENDOR_ID": "2dc8", "ID_MODEL_ID": "310b", "HID_ID": "0003:00002DC8:00006012"}
    assert vendor_product(props) == ("2dc8", "310b")


def test_vendor_product_falls_back_to_raw_hid_id():
    # Exactly the shape of a raw /sys/bus/hid/devices/*/uevent read (the
    # hidraw-liveness path) — no ID_VENDOR_ID/ID_MODEL_ID at all.
    props = {"HID_ID": "0003:00002DC8:00006012", "HID_NAME": "8BitDo Ultimate 2 Wireless Controller for PC"}
    assert vendor_product(props) == ("2dc8", "6012")


def test_profile_for_classifies_correctly_from_raw_sysfs_uevent_shape():
    # This is the actual bug reproduction: real 8BitDo Ultimate 2 uevent
    # content has no ID_VENDOR_ID/ID_MODEL_ID keys.
    raw_uevent_props = {
        "DRIVER": "hid-generic",
        "HID_ID": "0003:00002DC8:00006012",
        "HID_NAME": "8BitDo 8BitDo Ultimate 2 Wireless Controller for PC",
        "HID_PHYS": "usb-0000:07:00.0-2/input0",
        "HID_UNIQ": "950F5726DC",
    }
    profile = profile_for(raw_uevent_props)
    assert profile.id == "8bitdo"
    assert profile.device_class == "bitdo_dongle"


def test_is_candidate_hid_valve_vendor_via_hid_id_without_name_pattern():
    # v1's original heuristic (controller-liveness-watch.py) treated Valve
    # vendor ID alone as sufficient; only the name-pattern check was
    # previously ported here, dropping this net.
    assert is_candidate_hid({"HID_ID": "0003:000028DE:00001102", "HID_NAME": "Something Unusual"}) is True


class _FakeLoop:
    def __init__(self):
        self.calls = []

    def call_soon_threadsafe(self, fn, *args):
        self.calls.append((fn, args))


class _FakeDevice:
    def __init__(self, properties, parent=None, device_node=None):
        self.properties = properties
        self._parent = parent
        self.device_node = device_node

    def find_parent(self, subsystem):
        return self._parent if subsystem == "hid" else None


def test_udev_watcher_hands_off_via_call_soon_threadsafe_not_direct_call(tmp_path):
    # Direct regression test for the live crash: pyudev.MonitorObserver
    # calls _on_udev_event from its own thread, which has no asyncio event
    # loop. Calling feed() (-> asyncio.ensure_future) directly from there
    # raised "RuntimeError: no current event loop in thread 'Thread-1'"
    # the first time a real udev event arrived during testing 2026-08-20.
    fed = []

    def feed(event):
        fed.append(event)

    health = Health(path=Path(tmp_path) / "health.json")
    watcher = UdevWatcher(feed, health)
    fake_loop = _FakeLoop()
    watcher._loop = fake_loop

    device = _FakeDevice(
        {
            "SUBSYSTEM": "hid",
            "ID_INPUT_JOYSTICK": "1",
            "HID_UNIQ": "aa:bb:cc:dd:ee:ff",
            "ACTION": "add",
            "HID_NAME": "8BitDo Controller",
            "ID_VENDOR_ID": "2dc8",
        }
    )
    watcher._on_udev_event(device)

    # Must NOT have called feed synchronously from this (simulated pyudev) thread.
    assert fed == []
    assert len(fake_loop.calls) == 1
    fn, args = fake_loop.calls[0]
    assert fn is feed

    # Simulate the event loop actually running the scheduled callback.
    fn(*args)
    assert len(fed) == 1
    assert fed[0].device_id == "aa:bb:cc:dd:ee:ff"
    assert fed[0].kind == RawKind.ADD
    assert fed[0].device_class == "bitdo_dongle"


def test_udev_watcher_change_action_never_fires_a_connect(tmp_path):
    # Regression test for a real incident, 2026-08-30: pacman's own
    # 35-systemd-udev-reload.hook runs `udevadm trigger -c change` -- a
    # system-wide "change" event for every device -- as a PostTransaction
    # hook on ANY package install/upgrade/remove shipping files under
    # /usr/lib/udev/rules.d/* (this package's own reinstall included, but
    # not exclusively). With "change" previously treated as a fresh
    # connect, an already-connected, never-touched controller was
    # reported as a brand new physical connect, firing a real couch-mode
    # activation (screen unlock + CEC wake) with nobody having touched it.
    fed = []
    health = Health(path=Path(tmp_path) / "health.json")
    watcher = UdevWatcher(lambda e: fed.append(e), health)
    fake_loop = _FakeLoop()
    watcher._loop = fake_loop

    device = _FakeDevice(
        {
            "SUBSYSTEM": "hid",
            "ID_INPUT_JOYSTICK": "1",
            "HID_UNIQ": "950F5726DC",
            "ACTION": "change",
            "HID_NAME": "8BitDo Ultimate 2",
            "ID_VENDOR_ID": "2dc8",
        }
    )
    watcher._on_udev_event(device)

    assert fake_loop.calls == []


def test_udev_watcher_change_action_does_not_refire_a_cached_device_either(tmp_path):
    # Same bug, but for the more common real shape: a controller that's
    # been connected long enough to already have a devpath cache entry
    # (from its own earlier real "add") must not have that cache hit
    # reinterpreted as a fresh connect just because a later event happens
    # to carry ACTION=change -- the identity-cache path and the action ->
    # RawKind mapping are independent, and the old code applied the
    # add/change equivalence unconditionally after either path.
    fed = []
    health = Health(path=Path(tmp_path) / "health.json")
    watcher = UdevWatcher(lambda e: fed.append(e), health)
    fake_loop = _FakeLoop()
    watcher._loop = fake_loop

    devpath = "/devices/.../3-2/3-2:1.0/0003:2DC8:6012.0030/input/input88"
    add_event = _FakeDevice(
        {
            "SUBSYSTEM": "hid",
            "ID_INPUT_JOYSTICK": "1",
            "HID_UNIQ": "950F5726DC",
            "ACTION": "add",
            "HID_NAME": "8BitDo Ultimate 2",
            "ID_VENDOR_ID": "2dc8",
            "DEVPATH": devpath,
        }
    )
    watcher._on_udev_event(add_event)
    assert len(fake_loop.calls) == 1

    change_event = _FakeDevice(
        {
            "SUBSYSTEM": "hid",
            "ACTION": "change",
            "DEVPATH": devpath,
        }
    )
    watcher._on_udev_event(change_event)

    # Only the original "add" was ever scheduled -- the later system-wide
    # "change" retrigger for the same, already-cached devpath produced no
    # second event.
    assert len(fake_loop.calls) == 1


def test_udev_watcher_no_op_when_loop_not_set(tmp_path):
    fed = []
    health = Health(path=Path(tmp_path) / "health.json")
    watcher = UdevWatcher(lambda e: fed.append(e), health)
    # start() was never called (e.g. pyudev import failed) — _loop is None.
    device = _FakeDevice({"SUBSYSTEM": "hid", "ID_INPUT_JOYSTICK": "1", "HID_UNIQ": "aa:bb", "ACTION": "add"})
    watcher._on_udev_event(device)  # must not raise
    assert fed == []


def test_device_name_prefers_hid_name_falls_back_to_name():
    assert device_name({"HID_NAME": "8BitDo Ultimate 2"}) == "8BitDo Ultimate 2"
    # Input-subsystem child events carry the generic NAME key instead.
    assert device_name({"NAME": "8BitDo Ultimate 2 Wireless Controller for PC"}) == "8BitDo Ultimate 2 Wireless Controller for PC"
    assert device_name({}) == ""


def test_udev_watcher_merges_hid_parent_identity_for_input_subsystem_child_event(tmp_path):
    # Direct regression test for the second live-testing finding: the same
    # physical 8BitDo controller produced two device_ids (950F5726DC via
    # the hid subsystem, usb:2dc8:6012 via the input subsystem child node)
    # because the input-subsystem event has no HID_UNIQ/HID_NAME of its
    # own. Walking up to the hid parent must unify both into one device_id.
    fed = []
    health = Health(path=Path(tmp_path) / "health.json")
    watcher = UdevWatcher(lambda e: fed.append(e), health)
    fake_loop = _FakeLoop()
    watcher._loop = fake_loop

    hid_parent = _FakeDevice(
        {
            "SUBSYSTEM": "hid",
            "HID_UNIQ": "950F5726DC",
            "HID_NAME": "8BitDo 8BitDo Ultimate 2 Wireless Controller for PC",
        }
    )
    input_child_event = _FakeDevice(
        {
            "SUBSYSTEM": "input",
            "ACTION": "add",
            "ID_INPUT_JOYSTICK": "1",
            "ID_VENDOR_ID": "2dc8",
            "ID_MODEL_ID": "6012",
            "NAME": "8BitDo 8BitDo Ultimate 2 Wireless Controller for PC",
        },
        parent=hid_parent,
    )

    watcher._on_udev_event(input_child_event)
    fn, args = fake_loop.calls[0]
    fn(*args)

    assert len(fed) == 1
    # Must resolve to the HID parent's HID_UNIQ, not a usb:vid:pid fallback.
    assert fed[0].device_id == "950F5726DC"
    assert fed[0].device_class == "bitdo_dongle"


def test_udev_watcher_input_event_with_no_hid_parent_falls_back_to_vidpid(tmp_path):
    # A genuinely non-HID joystick (no hid-subsystem layer at all) must
    # still work via the ID_INPUT_JOYSTICK + vid:pid fallback path.
    fed = []
    health = Health(path=Path(tmp_path) / "health.json")
    watcher = UdevWatcher(lambda e: fed.append(e), health)
    fake_loop = _FakeLoop()
    watcher._loop = fake_loop

    device = _FakeDevice(
        {
            "SUBSYSTEM": "input",
            "ACTION": "add",
            "ID_INPUT_JOYSTICK": "1",
            "ID_VENDOR_ID": "abcd",
            "ID_MODEL_ID": "1234",
        },
        parent=None,
    )
    watcher._on_udev_event(device)
    fn, args = fake_loop.calls[0]
    fn(*args)

    assert len(fed) == 1
    assert fed[0].device_id == "usb:abcd:1234"


def test_udev_watcher_reuses_add_time_identity_when_parent_gone_on_remove(tmp_path):
    # Direct regression test for the third live-testing finding: on
    # disconnect, the HID parent's sysfs entry is sometimes already torn
    # down by the time the input-subsystem child's remove event arrives,
    # so find_parent("hid") returns None and the same physical device
    # splits back into a usb:vid:pid identity on the remove side even
    # though the connect side was correctly unified. The devpath cache
    # populated at ADD time must be reused here instead.
    fed = []
    health = Health(path=Path(tmp_path) / "health.json")
    watcher = UdevWatcher(lambda e: fed.append(e), health)
    fake_loop = _FakeLoop()
    watcher._loop = fake_loop

    child_devpath = "/devices/.../3-2/3-2:1.0/0003:2DC8:6012.0030/input/input88"
    hid_parent = _FakeDevice(
        {"SUBSYSTEM": "hid", "HID_UNIQ": "950F5726DC", "HID_NAME": "8BitDo Ultimate 2"}
    )
    add_event = _FakeDevice(
        {
            "SUBSYSTEM": "input",
            "ACTION": "add",
            "DEVPATH": child_devpath,
            "ID_INPUT_JOYSTICK": "1",
            "ID_VENDOR_ID": "2dc8",
            "ID_MODEL_ID": "6012",
        },
        parent=hid_parent,
    )
    watcher._on_udev_event(add_event)

    # Now the parent is gone by the time the matching remove arrives —
    # same devpath, but find_parent("hid") returns None this time.
    remove_event = _FakeDevice(
        {
            "SUBSYSTEM": "input",
            "ACTION": "remove",
            "DEVPATH": child_devpath,
            "ID_VENDOR_ID": "2dc8",
            "ID_MODEL_ID": "6012",
        },
        parent=None,
    )
    watcher._on_udev_event(remove_event)

    assert len(fake_loop.calls) == 2
    for fn, args in fake_loop.calls:
        fn(*args)

    assert len(fed) == 2
    assert fed[0].device_id == "950F5726DC" and fed[0].kind == RawKind.ADD
    assert fed[1].device_id == "950F5726DC" and fed[1].kind == RawKind.REMOVE
    assert fed[1].device_class == "bitdo_dongle"
    # Cache entry cleaned up after the remove.
    assert child_devpath not in watcher._resolved_by_devpath


def test_hidraw_liveness_watcher_add_tracks_device_class():
    fed = []
    watcher = HidrawLivenessWatcher(lambda e: fed.append(e))
    read_fd, write_fd = os.pipe()
    os.set_blocking(read_fd, False)  # hidraw fds are O_NONBLOCK
    try:
        watcher._fds[read_fd] = ("/dev/hidraw3", "950F5726DC", "bitdo_dongle")
        os.write(write_fd, b"\x01")
        watcher._on_readable(read_fd)
    finally:
        os.close(read_fd)
        os.close(write_fd)

    assert fed and fed[0].device_class == "bitdo_dongle"
    assert watcher._device_class_by_id["950F5726DC"] == "bitdo_dongle"


# --- Steam Controller Puck dock-status filtering ---
# Direct regression coverage for the 2026-08-22 live-testing finding:
# docking a Steam Controller to its charging puck (with the controller
# itself still powered off) fired a bare 2-byte 0x79 status report on the
# puck's hidraw interface, which the old code treated identically to real
# controller activity -- false-triggering couch mode just from charging.
# Confirmed via a live captured hidraw dump (see devices/detect.py's
# _is_status_only_report docstring for the exact evidence and byte shapes).

def test_is_status_only_report_true_for_steam_puck_dock_toggle():
    assert _is_status_only_report(bytes.fromhex("7901"), "steam_puck") is True
    assert _is_status_only_report(bytes.fromhex("7902"), "steam_puck") is True


def test_is_status_only_report_false_for_real_steam_puck_telemetry():
    # The captured header report (0x7b) and per-frame IMU reports (0x42)
    # that arrive during genuine power-on -- both must still count as
    # real activity.
    assert _is_status_only_report(bytes.fromhex("7b070002000000a307c3024eff"), "steam_puck") is False
    assert _is_status_only_report(bytes.fromhex("4200000000000000ff0f23031302"), "steam_puck") is False


def test_is_status_only_report_false_for_other_device_classes():
    # Scoped specifically to steam_puck -- a coincidental 2-byte 0x79
    # report from an unrelated controller class must not be filtered
    # (no evidence it means the same thing there).
    assert _is_status_only_report(bytes.fromhex("7901"), "bitdo_dongle") is False
    assert _is_status_only_report(bytes.fromhex("7901"), "generic") is False


def test_is_status_only_report_false_for_wrong_length():
    # Only the exact 2-byte shape observed live counts -- a longer report
    # that happens to start with 0x79 is not the dock-status pattern.
    assert _is_status_only_report(bytes.fromhex("790102030405"), "steam_puck") is False


def test_hidraw_liveness_watcher_ignores_dock_status_report_from_undocking(monkeypatch):
    # The actual reported bug, reproduced directly: a bare 0x79 report
    # from an otherwise-unseen device_id must not fire an ADD (couch mode
    # trigger) and must not update _last_seen.
    fed = []
    watcher = HidrawLivenessWatcher(lambda e: fed.append(e))
    read_fd, write_fd = os.pipe()
    os.set_blocking(read_fd, False)  # hidraw fds are O_NONBLOCK
    try:
        watcher._fds[read_fd] = ("/dev/hidraw10", "FXB99617010AC", "steam_puck")
        os.write(write_fd, bytes.fromhex("7901"))
        watcher._on_readable(read_fd)
    finally:
        os.close(read_fd)
        os.close(write_fd)

    assert fed == []
    assert "FXB99617010AC" not in watcher._reported_live
    assert "FXB99617010AC" not in watcher._last_seen


def test_hidraw_liveness_watcher_real_activity_still_fires_after_dock_status(monkeypatch):
    # A genuine power-on's real telemetry (arriving on the same or a
    # different hidraw node right after the dock-status blip) must still
    # trigger the ADD normally.
    fed = []
    watcher = HidrawLivenessWatcher(lambda e: fed.append(e))
    read_fd, write_fd = os.pipe()
    os.set_blocking(read_fd, False)  # hidraw fds are O_NONBLOCK
    try:
        watcher._fds[read_fd] = ("/dev/hidraw6", "FXB99617010AC", "steam_puck")
        os.write(write_fd, bytes.fromhex("4200000000000000ff0f23031302"))
        watcher._on_readable(read_fd)
    finally:
        os.close(read_fd)
        os.close(write_fd)

    assert len(fed) == 1
    assert fed[0].device_id == "FXB99617010AC"
    assert fed[0].kind == RawKind.ADD
    assert "FXB99617010AC" in watcher._reported_live


# --- Flooding/misbehaving hidraw device read-rate cap ---
# Direct regression coverage for the 2026-08-30 live incident: a hidraw
# node that never stops being readable (level-triggered `loop.add_reader`
# re-fires every loop iteration) drove 7.2M reads / 22.7GB in under an
# hour with no ceiling at all. See HidrawLivenessWatcher.__init__'s
# docstring comment for the full incident.

def _report_pipe():
    """A packetized, non-blocking fd pair: each send() is one report and each
    read returns exactly one, like a real hidraw node (a plain pipe would
    merge them into one read)."""
    import socket

    rx, tx = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    rx.setblocking(False)
    return rx, tx


def _watcher_with_node(**kwargs):
    fed = []
    watcher = HidrawLivenessWatcher(lambda e: fed.append(e), **kwargs)
    backed_off = []
    # Avoid needing a running asyncio loop in these synchronous tests --
    # _back_off_reader's own loop plumbing is exercised separately below.
    watcher._back_off_reader = lambda fd: backed_off.append(fd)
    rx, tx = _report_pipe()
    watcher._fds[rx.fileno()] = ("/dev/hidraw3", "950F5726DC", "bitdo_dongle")
    return watcher, fed, backed_off, rx, tx


def test_hidraw_liveness_watcher_flood_threshold_backs_off_and_stops_reading():
    watcher, fed, backed_off, rx, tx = _watcher_with_node(flood_reads_per_s=3, flood_backoff_s=10.0)
    with rx, tx:
        for _ in range(3):
            tx.send(b"\x01")
        watcher._on_readable(rx.fileno())
        assert backed_off == []

        # Four more reports in the same 1s window: the cap counts reports,
        # so the 4th one of the window trips it.
        tx.send(b"\x01")
        watcher._on_readable(rx.fileno())
        assert backed_off == [rx.fileno()]


def test_hidraw_liveness_watcher_flood_window_resets_after_a_second():
    watcher, fed, backed_off, rx, tx = _watcher_with_node(flood_reads_per_s=2, flood_backoff_s=10.0)
    with rx, tx:
        for _ in range(2):
            tx.send(b"\x01")
        watcher._on_readable(rx.fileno())
        assert backed_off == []

        # Simulate the window having elapsed instead of sleeping for real.
        watcher._read_window_start[rx.fileno()] -= 1.5
        for _ in range(2):
            tx.send(b"\x01")
        watcher._on_readable(rx.fileno())
        assert backed_off == []


def test_hidraw_liveness_watcher_drains_every_pending_report_in_one_wakeup():
    # ~400 reports/s with one read per wakeup is ~400 wakeups/s of overhead
    # and a flood counter measuring wakeups instead of the device.
    watcher, fed, backed_off, rx, tx = _watcher_with_node()
    with rx, tx:
        for _ in range(10):
            tx.send(b"\x01")
        watcher._on_readable(rx.fileno())
        assert watcher._read_window_count[rx.fileno()] == 10
        assert watcher.is_live("950F5726DC")


def test_hidraw_liveness_watcher_default_cap_tolerates_a_steam_puck_stream():
    # Issue #54: the Puck streams ~400 reports/s for a controller that is
    # on, even idle. That must not read as a flood.
    watcher, fed, backed_off, rx, tx = _watcher_with_node()
    assert watcher._flood_reads_per_s >= 1000
    with rx, tx:
        for _ in range(500):
            tx.send(b"\x01")
            watcher._on_readable(rx.fileno())
        assert backed_off == []


def test_hidraw_liveness_watcher_flooding_device_is_marked_live_while_backed_off():
    # Issue #55: the backoff window must not look like silence, or the
    # REMOVE timeout and owner-absence logic would act on a busy device.
    watcher, fed, backed_off, rx, tx = _watcher_with_node(flood_reads_per_s=1, flood_backoff_s=10.0)
    with rx, tx:
        for _ in range(3):
            tx.send(b"\x01")
        watcher._on_readable(rx.fileno())
        assert backed_off == [rx.fileno()]
        assert watcher.is_live("950F5726DC")
        assert watcher.seconds_since_activity("950F5726DC") < 1.0


def test_hidraw_liveness_watcher_flood_warning_is_logged_once_then_summarized(caplog):
    import logging

    watcher, fed, backed_off, rx, tx = _watcher_with_node(flood_reads_per_s=1, flood_backoff_s=10.0)
    with rx, tx, caplog.at_level(logging.WARNING, logger="joystick_notify.devices.detect"):
        for _ in range(5):
            watcher._read_window_start.pop(rx.fileno(), None)
            watcher._read_window_count.pop(rx.fileno(), None)
            for _ in range(3):
                tx.send(b"\x01")
            watcher._on_readable(rx.fileno())
    assert len(backed_off) == 5
    assert len([r for r in caplog.records if "exceeded" in r.message]) == 1


def test_hidraw_liveness_watcher_resume_reader_noop_if_device_already_gone():
    # A device can be removed by _rescan() (which closes the real fd) while
    # its reader is backed off -- the fd int the kernel then hands out next
    # could belong to something else entirely, so resuming must be a no-op
    # once _rescan() has dropped the fd from self._fds, not blindly
    # re-arm a stale/reused fd number.
    watcher = HidrawLivenessWatcher(lambda e: None)
    watcher._resume_reader(999)  # never in self._fds -- must not raise


def test_hidraw_liveness_watcher_remove_event_carries_correct_device_class():
    # The ADD path tracked device_class via self._fds, but the REMOVE path
    # (fired from the timeout loop, which only has device_id) previously
    # had no way to look it up and silently fell back to RawEvent's
    # "generic" default.
    fed = []
    watcher = HidrawLivenessWatcher(lambda e: fed.append(e))
    watcher._last_seen["950F5726DC"] = 0.0
    watcher._reported_live.add("950F5726DC")
    watcher._device_class_by_id["950F5726DC"] = "bitdo_dongle"

    # Same lookup-and-pop the timeout loop in _run() performs on removal.
    device_class = watcher._device_class_by_id.pop("950F5726DC", "generic")
    watcher._feed(
        RawEvent(device_id="950F5726DC", kind=RawKind.REMOVE, device_class=device_class, source="hidraw_liveness")
    )

    assert len(fed) == 1
    assert fed[0].device_class == "bitdo_dongle"


class _FakeUdevContext:
    def __init__(self, devices):
        self._devices = devices

    def list_devices(self, subsystem=None):
        return iter(self._devices)


def test_find_evdev_path_for_device_matches_via_hid_parent_identity(monkeypatch):
    import pyudev

    hid_parent = _FakeDevice({"HID_UNIQ": "FXB99617010AC"})
    matching_event = _FakeDevice(
        {"ID_INPUT_JOYSTICK": "1"}, parent=hid_parent, device_node="/dev/input/event7"
    )
    other_event = _FakeDevice(
        {"ID_INPUT_JOYSTICK": "1"}, parent=_FakeDevice({"HID_UNIQ": "someoneelse"}), device_node="/dev/input/event3"
    )
    monkeypatch.setattr(pyudev, "Context", lambda: _FakeUdevContext([other_event, matching_event]))

    assert find_evdev_path_for_device("FXB99617010AC") == "/dev/input/event7"


def test_find_evdev_path_for_device_skips_non_event_nodes(monkeypatch):
    import pyudev

    hid_parent = _FakeDevice({"HID_UNIQ": "FXB99617010AC"})
    js_node = _FakeDevice({"ID_INPUT_JOYSTICK": "1"}, parent=hid_parent, device_node="/dev/input/js2")
    monkeypatch.setattr(pyudev, "Context", lambda: _FakeUdevContext([js_node]))

    assert find_evdev_path_for_device("FXB99617010AC") is None


def test_find_evdev_path_for_device_returns_none_when_no_match(monkeypatch):
    import pyudev

    monkeypatch.setattr(pyudev, "Context", lambda: _FakeUdevContext([]))

    assert find_evdev_path_for_device("nonexistent") is None


# --- Identity of HID interfaces without HID_UNIQ (issue #55) ---
# The Steam Controller Puck's per-slot hidraw interfaces report an empty
# HID_UNIQ. The id used to be the sysfs node name ("0003:28DE:1304.000A"),
# which changes on every re-enumeration and which device_present() could
# never find, so the owner was "absent" for as long as the session ran.

PUCK_SLOT_UEVENT = {
    "HID_ID": "0003:000028DE:00001304",
    "HID_NAME": "Valve Software Steam Controller Puck",
    "HID_PHYS": "usb-0000:00:14.0-5/input2",
    "HID_UNIQ": "",
}


def test_stable_device_id_uses_hid_phys_when_uniq_is_empty():
    assert stable_device_id(PUCK_SLOT_UEVENT) == "phys:usb-0000:00:14.0-5/input2"


def test_stable_device_id_phys_is_independent_of_the_sysfs_node_counter():
    first = dict(PUCK_SLOT_UEVENT, DEVPATH="/devices/x/0003:28DE:1304.000A")
    after_replug = dict(PUCK_SLOT_UEVENT, DEVPATH="/devices/x/0003:28DE:1304.0014")
    assert stable_device_id(first) == stable_device_id(after_replug)


def test_stable_device_id_still_prefers_uniq_over_phys():
    assert stable_device_id(dict(PUCK_SLOT_UEVENT, HID_UNIQ="FXB99617010AC")) == "FXB99617010AC"


def _fake_hid_tree(tmp_path, entries):
    root = tmp_path / "hid"
    for name, lines in entries.items():
        d = root / name
        d.mkdir(parents=True)
        (d / "uevent").write_text("\n".join(lines) + "\n")
    return str(root)


def test_device_present_matches_uniq_exactly_not_as_a_substring(tmp_path):
    root = _fake_hid_tree(tmp_path, {"0003:2DC8:6013.001A": ["HID_UNIQ=950F5726DC9"]})
    assert device_present("950F5726DC", hid_root=root) is False
    assert device_present("950F5726DC9", hid_root=root) is True


def test_device_present_finds_a_phys_id(tmp_path):
    root = _fake_hid_tree(tmp_path, {"0003:28DE:1304.000A": ["HID_PHYS=usb-0000:00:14.0-5/input2", "HID_UNIQ="]})
    assert device_present("phys:usb-0000:00:14.0-5/input2", hid_root=root) is True
    assert device_present("phys:usb-0000:00:14.0-5/input3", hid_root=root) is False


def test_owner_present_phys_id_follows_liveness_not_the_dongle(tmp_path):
    root = _fake_hid_tree(tmp_path, {"0003:28DE:1304.000A": ["HID_PHYS=usb-0000:00:14.0-5/input2"]})
    phys = "phys:usb-0000:00:14.0-5/input2"
    # Dongle plugged in, controller silent: not present.
    assert owner_present(phys, lambda _id: False, hid_root=root) is False
    assert owner_present(phys, None, hid_root=root) is False
    # Reports flowing: present, whatever sysfs says.
    assert owner_present(phys, lambda _id: True, hid_root=root) is True


def test_owner_present_uniq_id_is_present_while_sysfs_lists_it(tmp_path):
    root = _fake_hid_tree(tmp_path, {"0003:2DC8:6013.001A": ["HID_UNIQ=950F5726DC"]})
    assert owner_present("950F5726DC", lambda _id: False, hid_root=root) is True
    assert owner_present("someone-else", lambda _id: False, hid_root=root) is False


def test_hidraw_candidate_nodes_use_the_stable_phys_id(tmp_path, monkeypatch):
    hid_root = tmp_path / "hid"
    node = hid_root / "0003:28DE:1304.000A"
    (node / "hidraw" / "hidraw0").mkdir(parents=True)
    (node / "uevent").write_text(
        "HID_ID=0003:000028DE:00001304\nHID_NAME=Valve Software Steam Controller Puck\n"
        "HID_PHYS=usb-0000:00:14.0-5/input2\nHID_UNIQ=\n"
    )
    monkeypatch.setattr("joystick_notify.devices.detect.HID_ROOT", str(hid_root))
    nodes = list(HidrawLivenessWatcher(lambda e: None)._find_candidate_nodes())
    assert nodes == [("/dev/hidraw0", "phys:usb-0000:00:14.0-5/input2", "steam_puck")]


def test_find_evdev_path_for_device_resolves_a_phys_id(monkeypatch):
    import pyudev

    hid_parent = _FakeDevice({"HID_UNIQ": "FXB99617010AC", "HID_PHYS": "usb-0000:00:14.0-5/input2"})
    node = _FakeDevice({"ID_INPUT_JOYSTICK": "1"}, parent=hid_parent, device_node="/dev/input/event3")
    monkeypatch.setattr(pyudev, "Context", lambda: _FakeUdevContext([node]))

    assert find_evdev_path_for_device("phys:usb-0000:00:14.0-5/input2") == "/dev/input/event3"
