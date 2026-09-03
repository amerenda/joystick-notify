#!/usr/bin/env bash
# sunshine-launch-steam-bigpicture.sh — per-app prep-cmd "do" for
# Sunshine's "Steam Big Picture" app entry specifically (not the global
# unlock hook — see sunshine-unlock-prep.sh, which already ran first for
# any app). Asks the daemon to launch Steam Big Picture via
# POST /api/launch/steam-bigpicture — the exact same
# launchers.launch_steam_bigpicture() a controller-triggered couch entry
# calls (config.on_connect="steam-bigpicture"), including its
# shutdown-existing-instance-first handling. Deliberately NOT
# `steam -gamepadui` run directly here — see that function's own
# docstring for the real, already-fixed race that would reintroduce.
#
# One-shot: no undo counterpart needed, nothing held across the stream.
set -euo pipefail

TOKEN_FILE="${HOME}/.config/joystick-notify/sunshine-api-token"
WIZARD_URL="http://127.0.0.1:8642/api/launch/steam-bigpicture"

if [ ! -r "$TOKEN_FILE" ]; then
  echo "sunshine-launch-steam-bigpicture: no API token at $TOKEN_FILE -- run the ansible sunshine role to provision one" >&2
  exit 1
fi

curl -fsS -X POST -H "Authorization: Bearer $(cat "$TOKEN_FILE")" "$WIZARD_URL" >/dev/null
