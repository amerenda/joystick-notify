from joystick_notify.devices.detect import is_candidate_hid, profile_for, stable_device_id
from joystick_notify.devices.profiles import GENERIC_PROFILE, match_profile


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
