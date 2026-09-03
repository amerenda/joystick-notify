#!/usr/bin/env bash
# sunshine-couch-undo.sh — Sunshine global_prep_cmd "undo" hook, runs once
# per stream end. See sunshine-couch-prep.sh for the full explanation;
# this is the same shape, calling /api/mode/desk instead
# (StateMachine.force_exit_to_desk()).
set -euo pipefail

TOKEN_FILE="${HOME}/.config/joystick-notify/sunshine-api-token"
WIZARD_URL="http://127.0.0.1:8642/api/mode/desk"

if [ ! -r "$TOKEN_FILE" ]; then
  echo "sunshine-couch-undo: no API token at $TOKEN_FILE -- run the ansible sunshine role to provision one" >&2
  exit 1
fi

curl -fsS -X POST -H "Authorization: Bearer $(cat "$TOKEN_FILE")" "$WIZARD_URL" >/dev/null
