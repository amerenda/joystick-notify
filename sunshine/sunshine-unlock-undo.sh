#!/usr/bin/env bash
# sunshine-unlock-undo.sh — Sunshine global_prep_cmd "undo" hook, runs
# once per stream end. See sunshine-unlock-prep.sh for the full
# explanation; this is the same shape, calling /api/screen/lock instead
# (screen_lock.activate_desk() — releases the inhibit, re-enables
# autolock; does not touch display/audio/cursor/CEC).
set -euo pipefail

TOKEN_FILE="${HOME}/.config/joystick-notify/sunshine-api-token"
WIZARD_URL="http://127.0.0.1:8642/api/screen/lock"

if [ ! -r "$TOKEN_FILE" ]; then
  echo "sunshine-unlock-undo: no API token at $TOKEN_FILE -- run the ansible sunshine role to provision one" >&2
  exit 1
fi

curl -fsS -X POST -H "Authorization: Bearer $(cat "$TOKEN_FILE")" "$WIZARD_URL" >/dev/null
