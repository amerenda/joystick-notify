"""Generic controller enumeration — the kernel-level layer that makes an
*unknown* controller work at all (profiles.py is cosmetic on top of this).

Two independent event sources feed the same `RawEvent` shape into the
debounce chokepoint:

1. `UdevWatcher` — observes the `input`/`hid` subsystems via pyudev, gated
   primarily on `ID_INPUT_JOYSTICK=1` (the kernel already classifies
   gamepads generically, protocol-agnostic). This is what v1's
   `udev/99-joystick-notify.rules` did via a spawned shell script per
   event; here it's one long-running observer feeding one in-memory
   pipeline instead.
2. `HidrawLivenessWatcher` — direct port of v1's
   `scripts/controller-liveness-watch.py`. Some receivers (confirmed
   2026-08-17 against the Steam Controller Puck receiver via live
   `udevadm monitor`) emit **zero** uevents on power-on/off — the receiver
   stays enumerated on USB the whole time and silently starts/stops
   relaying HID reports. No udev rule, however written, can catch that;
   only watching for actual HID report data flow on the `/dev/hidraw*`
   node can. Both sources feed the same debouncer, which already dedupes
   redundant events from overlapping sources within its window.

The important distinction from plans/joystick-notify-v2.md's tray-health
section: "no controller currently connected" is a normal idle Health.ok()
state. Only a broken *detection path itself* (pyudev context init failure,
permission denied reading /dev/input or /dev/hidraw*) is Health.failed() —
callers must not conflate the two.
"""
from __future__ import annotations

import asyncio
import glob
import logging
import os
import time
from dataclasses import dataclass
from typing import Callable

from ..debounce import RawEvent, RawKind
from ..health import Health
from ..supervisor import supervise
from .profiles import GENERIC_PROFILE, match_profile

logger = logging.getLogger(__name__)

HID_ROOT = "/sys/bus/hid/devices"
_NAME_PATTERNS = ("Controller", "Gamepad", "8BitDo")
_EXCLUDE_PATTERNS = ("LED", "Light", "Lighting")


def stable_device_id(properties: dict) -> str | None:
    """Prefer HID_UNIQ (Bluetooth MAC, or a vendor's own unique string) as
    the stable identifier — generalizes v1's lock_owner scheme. Falls back
    to `usb:{vid}:{pid}` for wired/dongle devices with no HID_UNIQ at all
    (v1 discovered the Steam Controller's HID_UNIQ has no colon, which
    broke *Bluetooth-shaped matching* against it — but as a device_id on
    its own, a bare HID_UNIQ or a vid:pid pair both work fine, matching was
    the only thing that was wrong).
    """
    uniq = properties.get("HID_UNIQ")
    if uniq:
        return uniq
    vid = properties.get("ID_VENDOR_ID")
    pid = properties.get("ID_MODEL_ID")
    if vid and pid:
        return f"usb:{vid}:{pid}"
    devpath = properties.get("DEVPATH")
    if devpath:
        return devpath
    return None


def parse_hid_id(hid_id: str) -> tuple[str, str] | None:
    """Parses the raw sysfs `HID_ID` field (format `bus:vendor:product`,
    each hex, e.g. `0003:00002DC8:00006012`) into lowercase 4-hex-digit
    vendor/product strings matching profiles.py's expected format. This is
    the *only* vendor/product signal present in a raw `/sys/bus/hid/devices/
    */uevent` file — `ID_VENDOR_ID`/`ID_MODEL_ID` are udev-database-computed
    properties that only exist on a live pyudev Monitor event, never in the
    static sysfs uevent file. Confirmed via live testing 2026-08-20: this
    gap silently classified every hidraw-liveness-detected controller as
    "generic", which is exactly backwards — hidraw-liveness is precisely
    the path used for the bounciest hardware (Steam Puck, 8BitDo dongle)
    that most needs correct per-class debounce timing.
    """
    parts = hid_id.split(":")
    if len(parts) != 3:
        return None
    _bus, vendor_hex, product_hex = parts
    try:
        vendor = f"{int(vendor_hex, 16) & 0xFFFF:04x}"
        product = f"{int(product_hex, 16) & 0xFFFF:04x}"
    except ValueError:
        return None
    return vendor, product


def vendor_product(properties: dict) -> tuple[str, str]:
    """Vendor/product lookup that works for both live pyudev Monitor
    properties (ID_VENDOR_ID/ID_MODEL_ID) and raw sysfs uevent dicts
    (HID_ID only) — see parse_hid_id() docstring."""
    vendor_id = (properties.get("ID_VENDOR_ID") or "").lower()
    product_id = (properties.get("ID_MODEL_ID") or "").lower()
    if vendor_id and product_id:
        return vendor_id, product_id
    parsed = parse_hid_id(properties.get("HID_ID", ""))
    if parsed:
        return parsed
    return vendor_id, product_id


def device_name(properties: dict) -> str:
    """HID-subsystem events carry the device name under `HID_NAME`;
    input-subsystem (evdev child) events carry the same string under the
    generic `NAME` key instead and never set `HID_NAME` at all. Checking
    only `HID_NAME` (the original port) silently failed name-pattern
    matching for every input-subsystem event — confirmed via live testing
    2026-08-20 against a real 8BitDo Ultimate 2."""
    return properties.get("HID_NAME") or properties.get("NAME", "")


def is_candidate_hid(properties: dict) -> bool:
    """ID_INPUT_JOYSTICK is the primary, protocol-agnostic signal (only
    present on live pyudev events, never in a raw sysfs uevent read). HID
    name-pattern matching and the Valve vendor-ID check are v1's original
    heuristics (controller-liveness-watch.py's is_candidate()), kept as a
    secondary net for HID-subsystem events that don't carry
    ID_INPUT_JOYSTICK at all — which is every event read from a raw sysfs
    uevent file, i.e. the entire hidraw-liveness detection path."""
    if properties.get("ID_INPUT_JOYSTICK") == "1":
        return True
    vendor_id, _ = vendor_product(properties)
    if vendor_id == "28de":  # Valve
        return True
    name = device_name(properties)
    if not name:
        return False
    if any(x in name for x in _EXCLUDE_PATTERNS):
        return False
    return any(p in name for p in _NAME_PATTERNS)


def device_present(device_id: str, hid_root: str = HID_ROOT, usb_root: str = "/sys/bus/usb/devices") -> bool:
    """Point-in-time presence check for a specific device_id, used by
    state_machine's owner-watch loop as defense-in-depth alongside the
    debounced connect/disconnect events (matches v1's `id_present`, which
    the no-controller-timeout logic in watcher-process.sh checked directly
    rather than relying solely on already-processed events).
    """
    if device_id.startswith("usb:"):
        _, vid, pid = device_id.split(":", 2)
        vid, pid = vid.lower(), pid.lower()
        for dev_dir in glob.glob(os.path.join(usb_root, "*")):
            vid_file = os.path.join(dev_dir, "idVendor")
            pid_file = os.path.join(dev_dir, "idProduct")
            if not (os.path.exists(vid_file) and os.path.exists(pid_file)):
                continue
            try:
                with open(vid_file) as f:
                    dev_vid = f.read().strip().lower()
                with open(pid_file) as f:
                    dev_pid = f.read().strip().lower()
            except OSError:
                continue
            if dev_vid == vid and dev_pid == pid:
                return True
        return False

    for uevent_path in glob.glob(os.path.join(hid_root, "*", "uevent")):
        try:
            with open(uevent_path) as f:
                content = f.read()
        except OSError:
            continue
        if f"HID_UNIQ={device_id}" in content:
            return True
    return False


def find_evdev_path_for_device(device_id: str) -> str | None:
    """Resolves an already-identified stable device_id (see
    stable_device_id()) back to its /dev/input/eventN node — for a
    component that needs to read raw button events from one *specific,
    already-known* device, not classify an unknown one. Currently only used
    by manual_exit.py's couch-mode-exit shortcut watcher, which only ever
    watches the current owner.

    Reuses the same HID-parent identity merge as UdevWatcher._on_udev_event
    (an evdev child node carries ID_INPUT_JOYSTICK but never HID_UNIQ; the
    stable identity lives on its HID-subsystem parent) so this resolves the
    same device_id the rest of the pipeline already uses.
    """
    try:
        import pyudev
    except ImportError:
        return None
    try:
        context = pyudev.Context()
    except Exception:
        return None
    for device in context.list_devices(subsystem="input"):
        devnode = device.device_node
        if not devnode or not os.path.basename(devnode).startswith("event"):
            continue
        properties = dict(device.properties)
        hid_parent = device.find_parent("hid")
        if hid_parent is not None:
            merged = dict(hid_parent.properties)
            merged.update(properties)
            properties = merged
        if stable_device_id(properties) == device_id:
            return devnode
    return None


def profile_for(properties: dict):
    vendor_id, product_id = vendor_product(properties)
    return match_profile(vendor_id=vendor_id, product_id=product_id, hid_name=device_name(properties))


@dataclass
class DeviceInfo:
    device_id: str
    hid_name: str
    profile_id: str
    device_class: str
    source: str


class UdevWatcher:
    """Long-running pyudev observer feeding RawEvents into a callback. Real
    hardware/udev dependent — exercised on real hardware, not in the unit
    test suite (see tests/test_debounce.py and test_state_machine.py for
    the parts of this pipeline that ARE unit-testable without hardware).

    pyudev.MonitorObserver delivers every event on its own background
    thread, never the asyncio event loop's thread — confirmed the hard way
    2026-08-20: calling `feed()` (which does `asyncio.ensure_future`)
    directly from that callback crashed with "no current event loop in
    thread 'Thread-1'" the first time a real udev event arrived during
    live testing. `_on_udev_event` must only ever hand off to the loop via
    `call_soon_threadsafe` — never call `self._feed` directly.
    """

    def __init__(self, feed: Callable[[RawEvent], None], health: Health) -> None:
        self._feed = feed
        self._health = health
        self._observer = None
        self._context = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # DEVPATH -> (device_id, device_class) resolved at ADD time, reused
        # on the matching REMOVE for the same devpath instead of
        # re-resolving from a tree that may be mid-teardown by then. Live
        # testing 2026-08-20 found the HID-parent walk (see _on_udev_event)
        # can fail on REMOVE even when it succeeded on the matching ADD —
        # the parent's sysfs entry is sometimes already gone by the time
        # an input-subsystem child's remove event is delivered, splitting
        # one physical disconnect back into two device_ids. Caching by the
        # event's own DEVPATH (stable across one connect session, since add
        # and remove for the same instance reference the same sysfs path)
        # closes that gap without needing the parent to still exist.
        self._resolved_by_devpath: dict[str, tuple[str, str]] = {}

    def start(self) -> None:
        try:
            import pyudev
        except ImportError as e:
            self._health.failed("devices", "pyudev not installed", str(e))
            return

        try:
            self._loop = asyncio.get_event_loop()
            self._context = pyudev.Context()
            monitor = pyudev.Monitor.from_netlink(self._context)
            monitor.filter_by(subsystem="input")
            monitor.filter_by(subsystem="hid")
            self._observer = pyudev.MonitorObserver(monitor, callback=self._on_udev_event)
            self._observer.start()
            self._health.ok("devices", "udev observer running")
        except Exception as e:
            # Permission denied on netlink socket, no udev running, etc. —
            # this is the "detection subsystem itself is broken" case from
            # the plan's tray table, distinct from "nothing connected."
            self._health.failed("devices", "udev observer failed to start", str(e))
            logger.exception("devices: udev observer failed to start")

    def _on_udev_event(self, device) -> None:
        # Runs on pyudev's MonitorObserver thread — see class docstring.
        # Keep this callback free of anything that touches the event loop
        # except the single call_soon_threadsafe handoff at the end.
        properties = dict(device.properties)
        devpath = properties.get("DEVPATH", "")
        action = properties.get("ACTION", "")

        cached = self._resolved_by_devpath.get(devpath) if devpath else None
        if cached is not None:
            # Reuse the identity resolved when THIS SAME devpath was added
            # — see _resolved_by_devpath's docstring in __init__ for why
            # re-deriving on REMOVE is unreliable.
            device_id, device_class = cached
            logger.debug("devices[%s]: reused cached identity for devpath %s", device_id, devpath)
        else:
            # A single physical controller fires TWO independent udev
            # events on connect: one from the "hid" subsystem (carries
            # HID_UNIQ/HID_NAME — the good identity) and one from the
            # "input" subsystem for its child evdev node (carries
            # ID_INPUT_JOYSTICK but never HID_UNIQ). Walk up to the HID
            # parent and merge its identity fields in, so both events for
            # the same physical device resolve to one device_id — confirmed
            # via live testing 2026-08-20 (8BitDo showed up as both
            # "950F5726DC" and "usb:2dc8:6012" before this fix).
            if properties.get("SUBSYSTEM") != "hid":
                hid_parent = device.find_parent("hid")
                if hid_parent is not None:
                    merged = dict(hid_parent.properties)
                    merged.update(properties)
                    properties = merged
                else:
                    logger.debug("devices: no HID parent found for devpath %s (non-hid subsystem event)", devpath)

            if not is_candidate_hid(properties):
                return
            device_id = stable_device_id(properties)
            if not device_id:
                return
            device_class = profile_for(properties).device_class

        if action == "add":
            kind = RawKind.ADD
        elif action == "remove":
            kind = RawKind.REMOVE
        else:
            # "change" deliberately does NOT count as a connect. Root
            # cause of a real incident 2026-08-30: pacman's own
            # 35-systemd-udev-reload.hook fires `udevadm trigger -c
            # change` -- a SYSTEM-WIDE "change" event for every device on
            # the box -- as a PostTransaction hook on ANY package install/
            # upgrade/remove that ships files under
            # /usr/lib/udev/rules.d/* (this package included, but not
            # exclusively -- any such package on the system, ansible-
            # deployed or not). With "change" treated as a fresh connect,
            # this handler saw the udev retrigger for an already-connected
            # controller (HID_UNIQ 950F5726DC, an 8BitDo pad -- the same
            # device this file's own test fixtures already use, from the
            # unrelated two-devpaths-one-controller fix) and reported it
            # as a brand new physical connect, firing a full couch-mode
            # activation -- screen unlock + real CEC wake to the TV --
            # with no controller having actually been touched. No test in
            # this file ever exercised the "change" branch and no comment
            # justified it; it was carried unchanged from the very first
            # v2 prototype commit. Genuine connects reliably fire "add"
            # (every existing test here uses "add"); only that should ever
            # start a new device's identity resolution.
            return

        if devpath:
            if kind == RawKind.ADD:
                self._resolved_by_devpath[devpath] = (device_id, device_class)
            else:
                self._resolved_by_devpath.pop(devpath, None)

        event = RawEvent(device_id=device_id, kind=kind, device_class=device_class, source="udev")
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._feed, event)

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer = None


# Confirmed via a live captured hidraw dump 2026-08-22 on a Steam
# Controller Puck: a bare 2-byte report (0x79 + a toggling status byte)
# fires on the dock's hidraw interface every time the controller is
# placed in or removed from the charging cradle -- completely independent
# of whether it's actually powered on. Docking to charge alone was enough
# to false-trigger couch mode before this filter existed (reported live
# 2026-08-22: "steam controller was offline, I connected it to the puck
# to charge it, and it triggered couch mode"). The same report ID also
# appears once at the very start of a REAL power-on, but in the same
# capture it was immediately followed (same instant) by the actual
# telemetry stream on different report IDs -- so filtering out 0x79
# specifically costs no perceptible responsiveness for a real connect, it
# just stops a bare dock-status blip from being mistaken for one on its
# own.
_STEAM_PUCK_STATUS_ONLY_REPORT_IDS = frozenset({0x79})


def _is_status_only_report(data: bytes, device_class: str) -> bool:
    if device_class != "steam_puck":
        return False
    return len(data) == 2 and data[0] in _STEAM_PUCK_STATUS_ONLY_REPORT_IDS


class HidrawLivenessWatcher:
    """Direct port of v1's controller-liveness-watch.py: watches actual HID
    report data flow on /dev/hidraw* for receivers that produce no uevents
    at all on power toggle. Runs as an asyncio task polling+selecting on
    the candidate fds, rather than a separate systemd unit as in v1 — one
    daemon, one event loop.
    """

    def __init__(
        self,
        feed: Callable[[RawEvent], None],
        health: Health | None = None,
        *,
        rescan_interval_s: float = 5.0,
        remove_timeout_s: float = 6.0,
    ) -> None:
        self._feed = feed
        self._health = health
        self._rescan_interval_s = rescan_interval_s
        self._remove_timeout_s = remove_timeout_s
        self._task: asyncio.Task | None = None
        self._fds: dict[int, tuple[str, str, str]] = {}  # fd -> (path, device_id, device_class)
        self._last_seen: dict[str, float] = {}
        self._reported_live: set[str] = set()
        # device_id -> device_class for devices currently reported live, so
        # the REMOVE event (fired from the timeout loop below, which only
        # has device_id) still carries the right class instead of silently
        # falling back to RawEvent's "generic" default.
        self._device_class_by_id: dict[str, str] = {}

    @staticmethod
    def _read_uevent(path: str) -> dict:
        values: dict[str, str] = {}
        try:
            with open(os.path.join(path, "uevent")) as f:
                for line in f:
                    if "=" in line:
                        key, _, value = line.rstrip("\n").partition("=")
                        values[key] = value
        except OSError:
            pass
        return values

    def _find_candidate_nodes(self):
        for hid_path in sorted(glob.glob(os.path.join(HID_ROOT, "*"))):
            uevent = self._read_uevent(hid_path)
            if not is_candidate_hid(uevent):
                continue
            device_id = stable_device_id(uevent) or os.path.basename(hid_path)
            profile = profile_for(uevent)
            for node in sorted(glob.glob(os.path.join(hid_path, "hidraw", "hidraw*"))):
                yield "/dev/" + os.path.basename(node), device_id, profile.device_class

    def start(self) -> None:
        if self._health is not None:
            self._task = supervise("hidraw_liveness", self._run(), self._health)
        else:
            self._task = asyncio.ensure_future(self._run())

    async def _run(self) -> None:
        loop = asyncio.get_event_loop()
        last_scan = 0.0
        try:
            while True:
                now = time.monotonic()
                if now - last_scan >= self._rescan_interval_s:
                    last_scan = now
                    self._rescan(loop)

                if not self._fds:
                    await asyncio.sleep(1.0)
                else:
                    await asyncio.sleep(0.5)

                now = time.monotonic()
                for device_id in list(self._reported_live):
                    if now - self._last_seen.get(device_id, 0.0) > self._remove_timeout_s:
                        self._reported_live.discard(device_id)
                        device_class = self._device_class_by_id.pop(device_id, "generic")
                        self._feed(RawEvent(device_id=device_id, kind=RawKind.REMOVE, device_class=device_class, source="hidraw_liveness"))
        except asyncio.CancelledError:
            pass
        finally:
            for fd in list(self._fds):
                self._safe_remove_reader(loop, fd)

    def _rescan(self, loop: asyncio.AbstractEventLoop) -> None:
        wanted = {path: (device_id, device_class) for path, device_id, device_class in self._find_candidate_nodes()}
        open_paths = {path for path, _, _ in self._fds.values()}

        for path, (device_id, device_class) in wanted.items():
            if path in open_paths:
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            self._fds[fd] = (path, device_id, device_class)
            loop.add_reader(fd, self._on_readable, fd)

        for fd, (path, _device_id, _device_class) in list(self._fds.items()):
            if path not in wanted:
                self._safe_remove_reader(loop, fd)
                del self._fds[fd]

    def _safe_remove_reader(self, loop: asyncio.AbstractEventLoop, fd: int) -> None:
        try:
            loop.remove_reader(fd)
            os.close(fd)
        except OSError:
            pass

    def _on_readable(self, fd: int) -> None:
        path, device_id, device_class = self._fds.get(fd, (None, None, None))
        if device_id is None:
            return
        try:
            data = os.read(fd, 4096)
        except OSError:
            return
        if not data:
            return
        if _is_status_only_report(data, device_class):
            logger.debug(
                "devices[%s]: ignoring status-only report %s (dock presence/charge toggle, not real activity)",
                device_id, data.hex(),
            )
            return
        self._last_seen[device_id] = time.monotonic()
        if device_id not in self._reported_live:
            self._reported_live.add(device_id)
            self._device_class_by_id[device_id] = device_class
            self._feed(RawEvent(device_id=device_id, kind=RawKind.ADD, device_class=device_class, source="hidraw_liveness"))

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
