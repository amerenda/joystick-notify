#!/usr/bin/env bash
# jn-setup-root-carveout.sh — one-time interactive root setup, run by hand
# after installing the joystick-notify package:
#   sudo jn-setup-root-carveout.sh
#
# Deliberately NOT run automatically by pacman's install scriptlet: a
# scriptlet runs as root during package install/upgrade with no reliable
# notion of "the desktop user this package is being set up for" (chroot
# installs, SSH-driven installs, etc. all break that assumption) — the same
# reason v1's own install.sh required an interactive `sudo -v` at the top
# instead of being wired into a package hook. This script installs the
# narrow root carve-out described in plans/joystick-notify-v2.md decision
# #3: a NOPASSWD sudoers rule scoped to exactly two commands (DRM connector
# rescan, CEC self-heal), plus the root-owned CEC system units.
set -euo pipefail

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  echo "[joystick-notify] Re-running under sudo for the root-level setup steps..."
  exec sudo "$0" "${SUDO_USER:-$(id -un)}"
fi

TARGET_USER="${1:-${SUDO_USER:-}}"
if [ -z "$TARGET_USER" ]; then
  echo "Usage: sudo jn-setup-root-carveout.sh <username>" >&2
  exit 2
fi

echo "[joystick-notify] Installing sudoers rule for $TARGET_USER (DRM rescan + CEC self-heal, NOPASSWD, narrowly scoped) ..."
sed "s/%USER%/$TARGET_USER/g" /usr/share/joystick-notify/joystick-notify.sudoers > /etc/sudoers.d/joystick-notify
chmod 0440 /etc/sudoers.d/joystick-notify
visudo -cf /etc/sudoers.d/joystick-notify

echo "[joystick-notify] Reloading udev rules ..."
udevadm control --reload-rules

echo "[joystick-notify] Enabling root-level CEC system units ..."
systemctl daemon-reload
systemctl enable cec-fixup.service >/dev/null 2>&1 || true
systemctl enable --now cec-watchdog.timer

echo "[joystick-notify] Done. Now enable the user-level daemon (as $TARGET_USER, not root):"
echo "  systemctl --user enable --now joystick-notify.service"
echo "  systemctl --user enable --now joystick-notify-tray.service   # optional, needs PyQt6"
