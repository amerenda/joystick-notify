#!/usr/bin/env bash
# sunshine-exit-steam-bigpicture.sh — per-app prep-cmd "undo" for
# Sunshine's "Steam Big Picture" app entry (see
# sunshine-launch-steam-bigpicture.sh for the "do" half). The app entry
# has no `cmd` for Sunshine to track and kill itself, so `undo` is the
# only hook Sunshine runs when a Moonlight client hits "stop app" or
# disconnects. Asks the daemon to exit Big Picture via
# POST /api/exit/steam-bigpicture -- the exact same
# launchers.exit_launched("steam-bigpicture") the desk/couch mode-switch
# teardown already calls (nice `steam -shutdown`, PID-tracked).
set -euo pipefail

TOKEN_FILE="${HOME}/.config/joystick-notify/sunshine-api-token"
WIZARD_URL="http://127.0.0.1:8642/api/exit/steam-bigpicture"

if [ ! -r "$TOKEN_FILE" ]; then
  echo "sunshine-exit-steam-bigpicture: no API token at $TOKEN_FILE -- run the ansible sunshine role to provision one" >&2
  exit 1
fi

curl -fsS -X POST -H "Authorization: Bearer $(cat "$TOKEN_FILE")" "$WIZARD_URL" >/dev/null
