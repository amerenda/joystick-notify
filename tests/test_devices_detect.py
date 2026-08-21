import os
from pathlib import Path

from joystick_notify.debounce import RawEvent, RawKind
from joystick_notify.devices.detect import (
    HidrawLivenessWatcher,
    UdevWatcher,
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
    try:
        watcher._fds[read_fd] = ("/dev/hidraw3", "950F5726DC", "bitdo_dongle")
        os.write(write_fd, b"\x01")
        watcher._on_readable(read_fd)
    finally:
        os.close(read_fd)
        os.close(write_fd)

    assert fed and fed[0].device_class == "bitdo_dongle"
    assert watcher._device_class_by_id["950F5726DC"] == "bitdo_dongle"


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
