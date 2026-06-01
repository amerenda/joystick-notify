#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<'EOF'
Usage: install.sh [--no-enable]

Installs:
- scripts to /usr/local/bin
- udev rule to /etc/udev/rules.d
- systemd user unit to ~/.config/systemd/user

Options:
  --no-enable   Do not enable/start the systemd user service
EOF
}

ENABLE=1
for arg in "$@"; do
  case "$arg" in
    --no-enable) ENABLE=0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "Missing command: $1" >&2; exit 1; }; }
need_cmd install
need_cmd systemctl
need_cmd sudo

if [ "${EUID:-$(id -u)}" -eq 0 ]; then
  echo "[joystick-notify] Do not run this installer with sudo (it needs your user session bus for systemctl --user)." >&2
  echo "Run as your user instead:" >&2
  echo "  $ROOT/$(basename "$0") $*" >&2
  exit 2
fi

# Prompt for sudo once up front (interactive).
sudo -v

echo "[joystick-notify] Installing scripts to /usr/local/bin ..."
sudo install -Dm0755 "$ROOT/scripts/monitor-switcher.sh" /usr/local/bin/monitor-switcher.sh
sudo install -Dm0755 "$ROOT/scripts/joystick-event.sh" /usr/local/bin/joystick-event.sh
sudo install -Dm0755 "$ROOT/scripts/launch-bigpicture.sh" /usr/local/bin/launch-bigpicture.sh
sudo install -Dm0755 "$ROOT/scripts/game-wrapper.sh" /usr/local/bin/game-wrapper.sh
sudo install -Dm0755 "$ROOT/scripts/force-desk-primary.sh" /usr/local/bin/force-desk-primary.sh
sudo install -Dm0755 "$ROOT/scripts/couch-switch.sh" /usr/local/bin/couch-switch.sh
sudo install -Dm0755 "$ROOT/scripts/check-gpu-connectors.sh" /usr/local/bin/check-gpu-connectors.sh
sudo install -Dm0755 "$ROOT/system-tray/joystick-tray.py" /usr/local/bin/joystick-notify-tray

echo "[joystick-notify] Installing library components to /usr/local/lib/joystick-notify ..."
sudo mkdir -p /usr/local/lib/joystick-notify
sudo install -Dm0644 "$ROOT"/lib/*.sh /usr/local/lib/joystick-notify/

# Optional legacy launcher (kept only if present in the repo)
if [ -f "$ROOT/steam-bigpicture-primary.sh" ]; then
  sudo install -Dm0755 "$ROOT/steam-bigpicture-primary.sh" /usr/local/bin/steam-bigpicture-primary.sh
fi

echo "[joystick-notify] Installing tmpfiles.d (runtime directory creation at boot) ..."
sudo install -Dm0644 "$ROOT/tmpfiles/joystick-notify.conf" /etc/tmpfiles.d/joystick-notify.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/joystick-notify.conf

echo "[joystick-notify] Installing udev rules ..."
sudo install -Dm0644 "$ROOT/udev/99-joystick-notify.rules" /etc/udev/rules.d/99-joystick-notify.rules
sudo install -Dm0644 "$ROOT/udev/98-monitor-hotplug.rules" /etc/udev/rules.d/98-monitor-hotplug.rules
sudo udevadm control --reload-rules

echo "[joystick-notify] Installing systemd user unit ..."
install -Dm0644 "$ROOT/systemd/joystick-notify.service" "$HOME/.config/systemd/user/joystick-notify.service"
install -Dm0644 "$ROOT/systemd/joystick-notify-steam-shutdown.service" "$HOME/.config/systemd/user/joystick-notify-steam-shutdown.service"
install -Dm0644 "$ROOT/systemd/joystick-notify-steam-shutdown.path" "$HOME/.config/systemd/user/joystick-notify-steam-shutdown.path"
install -Dm0644 "$ROOT/systemd/joystick-notify-tray.service" "$HOME/.config/systemd/user/joystick-notify-tray.service"
install -Dm0644 "$ROOT/systemd/force-desk-primary.service" "$HOME/.config/systemd/user/force-desk-primary.service"

echo "[joystick-notify] Installing desktop entry (tray app id)..."
install -Dm0644 "$ROOT/system-tray/joystick-notify-tray.desktop" "$HOME/.local/share/applications/joystick-notify-tray.desktop"

# Ensure user bus vars exist (some terminals / sudo contexts can be missing them).
uid="$(id -u)"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$uid}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=${XDG_RUNTIME_DIR}/bus}"

systemctl --user daemon-reload

if [ "$ENABLE" -eq 1 ]; then
  echo "[joystick-notify] Enabling + starting systemd user service ..."
  systemctl --user enable --now joystick-notify.service
  systemctl --user enable --now joystick-notify-steam-shutdown.path
  if python3 -c 'import PyQt6' >/dev/null 2>&1; then
    systemctl --user enable --now joystick-notify-tray.service
  else
    echo "[joystick-notify] NOTE: tray icon not enabled (missing PyQt6). Install: pacman -S --needed python-pyqt6"
  fi
  echo "[joystick-notify] Restarting systemd user service to pick up updates ..."
  systemctl --user restart joystick-notify.service
else
  if systemctl --user is-active --quiet joystick-notify.service; then
    echo "[joystick-notify] Service is running; restarting to pick up updates ..."
    systemctl --user restart joystick-notify.service
  fi
  if systemctl --user is-active --quiet joystick-notify-steam-shutdown.path; then
    echo "[joystick-notify] Steam shutdown watcher is running; restarting to pick up updates ..."
    systemctl --user restart joystick-notify-steam-shutdown.path
  fi
  if systemctl --user is-active --quiet joystick-notify-tray.service; then
    echo "[joystick-notify] Tray icon service is running; restarting to pick up updates ..."
    systemctl --user restart joystick-notify-tray.service
  fi
  echo "[joystick-notify] Installed. To enable later:"
  echo "  systemctl --user enable --now joystick-notify.service"
  echo "  systemctl --user enable --now joystick-notify-steam-shutdown.path"
  echo "  systemctl --user enable --now joystick-notify-tray.service"
fi

echo "[joystick-notify] Done."


