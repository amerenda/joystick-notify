#!/usr/bin/env python3
"""controller-liveness-watch.py

Some controller receivers (confirmed 2026-08-17 via live `udevadm monitor`:
Valve's Steam Controller "Puck" wireless receiver) generate NO udev event at
all -- not add, not change, nothing -- when the controller itself powers
on/off while the receiver stays plugged into USB. The receiver's own
firmware silently starts/stops relaying HID reports with no kernel-visible
device/subsystem state change. udev/99-joystick-notify.rules can only react
to real uevents, so for hardware like this, event-driven detection is
structurally incapable of ever firing -- no udev rule, however written,
could catch this.

This watches for actual HID report *data flow* instead, using the same
vendor/name matching criteria as the udev rule (Valve vendor ID 28DE, or
HID_NAME containing Controller/Gamepad/8BitDo) so it is not hardcoded to one
controller model. Idle -> active transitions synthesize an "add" event into
the same events.log pipeline joystick-event.sh already writes to (reusing
its existing debounce/locking, not duplicating it), and a period of silence
synthesizes "remove". If a device *does* also fire a real udev event (e.g.
the receiver itself being unplugged/replugged), that path still works too --
joystick-event.sh's own debounce logic already discards the redundant
duplicate within its 5s window, so both mechanisms coexisting is safe.

Reads from each HID device's /dev/hidraw* node, not its evdev /dev/input/event*
nodes. Confirmed by direct live test (2026-08-17): once Steam has claimed a
controller, it holds an exclusive-ish grip on the evdev path such that a
second reader's fd opens fine but never observes any data, even during
active, confirmed input -- hidraw is what Steam itself reads raw reports
from, is not exclusive, and did show data immediately in the same live test.

Runs as a permanent background watcher (see systemd/controller-liveness-watch.service)
rather than a one-shot udev RUN+= action, since it needs to keep sampling for
data indefinitely, not react to a single event.
"""
import glob
import os
import select
import subprocess
import time

HID_ROOT = "/sys/bus/hid/devices"
JOYSTICK_EVENT = "/usr/local/bin/joystick-event.sh"
REMOVE_TIMEOUT = float(os.environ.get("CONTROLLER_LIVENESS_REMOVE_TIMEOUT", "6"))
RESCAN_INTERVAL = float(os.environ.get("CONTROLLER_LIVENESS_RESCAN_INTERVAL", "5"))


def read_uevent(path):
    values = {}
    try:
        with open(os.path.join(path, "uevent")) as f:
            for line in f:
                if "=" in line:
                    key, _, value = line.rstrip("\n").partition("=")
                    values[key] = value
    except OSError:
        pass
    return values


def is_candidate(uevent):
    # Same matching criteria as udev/99-joystick-notify.rules: vendor-based
    # (Valve 28DE) or name-pattern-based, not any specific product ID.
    if "28DE" in uevent.get("HID_ID", "").upper():
        return True
    name = uevent.get("HID_NAME", "")
    return any(pat in name for pat in ("Controller", "Gamepad", "8BitDo"))


def find_event_nodes():
    """Yields (devid, hidraw_node_path) for every candidate HID device."""
    for hid in sorted(glob.glob(os.path.join(HID_ROOT, "*"))):
        uevent = read_uevent(hid)
        if not is_candidate(uevent):
            continue
        devid = uevent.get("HID_UNIQ") or os.path.basename(hid)
        for node in sorted(glob.glob(os.path.join(hid, "hidraw", "hidraw*"))):
            yield devid, "/dev/" + os.path.basename(node)


def fire(devid, action):
    env = dict(os.environ)
    env["ACTION"] = action
    subprocess.Popen(
        [JOYSTICK_EVENT, devid],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main():
    fds = {}  # fd -> (path, devid)
    last_seen = {}  # devid -> monotonic time of last data
    reported_live = set()
    last_scan = 0.0

    while True:
        now = time.monotonic()

        if now - last_scan >= RESCAN_INTERVAL:
            last_scan = now
            wanted = {path: devid for devid, path in find_event_nodes()}
            open_paths = {p for p, _ in fds.values()}

            for path, devid in wanted.items():
                if path in open_paths:
                    continue
                try:
                    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                except OSError:
                    continue
                fds[fd] = (path, devid)

            for fd, (path, _devid) in list(fds.items()):
                if path not in wanted:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                    del fds[fd]

        if fds:
            try:
                readable, _, _ = select.select(list(fds.keys()), [], [], 1.0)
            except OSError:
                readable = []
            for fd in readable:
                try:
                    data = os.read(fd, 4096)
                except OSError:
                    continue
                if not data:
                    continue
                _, devid = fds[fd]
                last_seen[devid] = now
                if devid not in reported_live:
                    reported_live.add(devid)
                    fire(devid, "add")
        else:
            time.sleep(1.0)

        for devid in list(reported_live):
            if now - last_seen.get(devid, 0.0) > REMOVE_TIMEOUT:
                reported_live.discard(devid)
                fire(devid, "remove")


if __name__ == "__main__":
    main()
