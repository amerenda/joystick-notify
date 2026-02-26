#!/usr/bin/env bash
# check-gpu-connectors.sh - See what the GPU reports for each display connector.
# Use this to verify the GPU sees both HDMI cables (e.g. desk + receiver).
# No couch/desk logic; read-only check of /sys/class/drm.

set -euo pipefail

echo "DRM connector status (what the GPU sees):"
echo "----------------------------------------"
for c in /sys/class/drm/card*-HDMI-* /sys/class/drm/card*-DP-*; do
    [ -d "$c" ] || continue
    [ -f "$c/status" ] || continue
    name="${c##*/}"
    status="$(cat "$c/status" 2>/dev/null || echo "?")"
    echo "  $name: $status"
done
echo "----------------------------------------"
echo ""
echo "If the port that goes to your receiver shows 'disconnected', the GPU does not"
echo "see a display on that port (often EDID read failed - receiver not responding on DDC)."
echo "Try:"
echo "  1. Power on the receiver and set it to the PC input (e.g. HDMI 2)."
echo "  2. Wait 10–15 s, then force a rescan:"
echo "     sudo udevadm trigger --action=change /sys/class/drm/card1-HDMI-A-1"
echo "     (adjust card/connector if needed). Run this script again."
echo "  3. Check dmesg: dmesg | grep -iE 'EDID|HDMI-A-1|amdgpu.*ERROR'"
echo "     If you see 'EDID err: 2', the receiver is not responding on the EDID line;"
echo "     some receivers never present EDID to the PC (hardware limitation)."
echo "  4. Hardware workaround: an HDMI EDID emulator between PC and receiver can"
echo "     always present a display to the PC so the connector stays 'connected'."
