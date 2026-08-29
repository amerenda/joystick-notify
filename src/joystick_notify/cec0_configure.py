"""Root-owned helper invoked by packaging/udev/cec-configure-autostart.rules
when /dev/cec0 appears. Configures the adapter's physical address from the
couch output's EDID and its logical address as a playback device — direct
port of v1's cec0-configure@.service, generalized to read the couch port
from config.toml at runtime instead of a connector name hardcoded into the
udev rule itself (v1's card1-HDMI-A-1, specific to Alex's GPU/card index).
"""
from __future__ import annotations

import glob
import os
import socket
import sys

from .config import store as config_store


def find_edid_path(couch_port: str) -> str | None:
    for card_dir in sorted(glob.glob(f"/sys/class/drm/card*-{couch_port}")):
        edid = os.path.join(card_dir, "edid")
        if os.path.exists(edid):
            return edid
    return None


def main() -> int:
    config = config_store.load()
    if not config.cec.enabled or not config.display.couch_port:
        print("cec0-configure: CEC disabled or couch port not configured, skipping", file=sys.stderr)
        return 0

    edid_path = find_edid_path(config.display.couch_port)
    if edid_path is None:
        print(f"cec0-configure: no DRM connector found for couch port {config.display.couch_port}", file=sys.stderr)
        return 1

    osd_name = socket.gethostname()
    cmd = [
        "cec-ctl",
        "--device=0",
        f"--osd-name={osd_name}",
        "--playback",
        f"--phys-addr-from-edid-poll={edid_path}",
    ]
    os.execvp(cmd[0], cmd)  # replaces this process — matches v1's Type=exec unit
    return 0  # unreachable; execvp only returns on failure (raises OSError)


if __name__ == "__main__":
    sys.exit(main())
