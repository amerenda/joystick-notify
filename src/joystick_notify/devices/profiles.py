"""Known controller vendor/product database — data, not logic. This is the
*cosmetic* identity layer only (friendly names, icons, per-profile debounce
defaults): a device that misses every entry here still works via the
kernel `ID_INPUT_JOYSTICK` layer in `detect.py`, it just shows up as
"Unknown Controller" in the wizard instead of "8BitDo Ultimate 2". See
plans/joystick-notify-v2.md, "Controller detection strategy" — detection
was never supposed to depend on matching a specific vendor pattern.

VID/PID values below are the ones already confirmed against real hardware
in v1's udev rules (`udev/99-joystick-notify.rules`,
`scripts/controller-liveness-watch.py`) plus well-known public IDs for the
other families the plan calls out to cover. Treat anything not already
confirmed against v1 as a best-effort starting point, not gospel — flag
for verification against a real device before shipping, not guessed
silently as fact.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ControllerProfile:
    id: str
    name: str
    vendor_ids: frozenset[str] = frozenset()
    product_ids: frozenset[str] = frozenset()
    name_patterns: tuple[str, ...] = ()
    # Debounce-timing bucket (see config.schema.TimingConfig.debounce_per_class_ms)
    # — the Steam Puck receiver and an 8BitDo dongle don't necessarily
    # bounce identically (per the v1 audit), so profiles are grouped by
    # observed bounce behavior, not just by vendor.
    device_class: str = "generic"


GENERIC_PROFILE = ControllerProfile(id="generic", name="Unknown Controller", device_class="generic")

PROFILES: tuple[ControllerProfile, ...] = (
    ControllerProfile(
        id="dualshock4",
        name="Sony DualShock 4",
        vendor_ids=frozenset({"054c"}),
        product_ids=frozenset({"05c4", "09cc"}),
        device_class="sony",
    ),
    ControllerProfile(
        id="dualsense",
        name="Sony DualSense",
        vendor_ids=frozenset({"054c"}),
        product_ids=frozenset({"0ce6", "0df2"}),
        device_class="sony",
    ),
    ControllerProfile(
        id="xbox_wireless",
        name="Xbox One/Series Controller",
        vendor_ids=frozenset({"045e"}),
        device_class="xbox",
    ),
    ControllerProfile(
        id="switch_pro",
        name="Nintendo Switch Pro Controller",
        vendor_ids=frozenset({"057e"}),
        product_ids=frozenset({"2009"}),
        device_class="nintendo",
    ),
    ControllerProfile(
        id="joycon",
        name="Nintendo Joy-Con",
        vendor_ids=frozenset({"057e"}),
        product_ids=frozenset({"2006", "2007"}),
        device_class="nintendo",
    ),
    ControllerProfile(
        # Scoped to Valve's vendor ID rather than a specific product ID —
        # per v1's discovery, this durably covers wired, dongle/Puck, and
        # Bluetooth connection methods without needing an update per
        # hardware revision. See detect.py's stable_device_id() for why the
        # Puck receiver also needs the hidraw-liveness fallback, not just
        # this profile match.
        id="steam_controller",
        name="Valve Steam Controller",
        vendor_ids=frozenset({"28de"}),
        name_patterns=("Controller",),
        device_class="steam_puck",
    ),
    ControllerProfile(
        # v1 confirmed multiple 2dc8:XXXX product IDs across dongle
        # modes/firmware states (3106, 3109, 310b, 6012, 6013) — matched
        # here on vendor + name pattern rather than enumerating every
        # product ID, since 8BitDo's dongle mode/PID drifts by firmware.
        id="8bitdo",
        name="8BitDo Controller",
        vendor_ids=frozenset({"2dc8"}),
        name_patterns=("8BitDo",),
        device_class="bitdo_dongle",
    ),
)


def match_profile(vendor_id: str = "", product_id: str = "", hid_name: str = "") -> ControllerProfile:
    vendor_id = vendor_id.lower()
    product_id = product_id.lower()
    for profile in PROFILES:
        if profile.vendor_ids and vendor_id not in profile.vendor_ids:
            continue
        if profile.vendor_ids and profile.product_ids and product_id not in profile.product_ids:
            continue
        if profile.name_patterns and not any(p in hid_name for p in profile.name_patterns):
            continue
        if not profile.vendor_ids and not profile.name_patterns:
            continue
        return profile
    return GENERIC_PROFILE
