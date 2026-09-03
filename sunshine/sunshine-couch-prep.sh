#!/usr/bin/env bash
# sunshine-couch-prep.sh — Sunshine global_prep_cmd "do" hook, runs once
# per stream start (any app). Undo counterpart: sunshine-couch-undo.sh.
#
# Deliberately thin: this does NOT reimplement screen-unlock/display/
# audio/cursor/CEC logic. It just asks the already-running joystick-notify
# daemon to enter couch mode via its wizard's local API
# (POST /api/mode/couch) — the exact same code path
# (StateMachine.force_enter_couch()) a controller connect triggers. See
# src/joystick_notify/wizard/server.py's api_mode_couch and
# actions/screen_lock.py for what actually happens on the other end.
#
# Requires an API token installed via `jn-daemon --install-api-token`
# (provisioned by ansible-playbooks' roles/sunshine, value from BWS) —
# see wizard/auth.py's install_api_token() for why this is a purpose-
# built token, not the wizard's own admin login password.
set -euo pipefail

TOKEN_FILE="${HOME}/.config/joystick-notify/sunshine-api-token"
# Must match config.toml's [wizard] port (default 8642) on this host.
WIZARD_URL="http://127.0.0.1:8642/api/mode/couch"

if [ ! -r "$TOKEN_FILE" ]; then
  echo "sunshine-couch-prep: no API token at $TOKEN_FILE -- run the ansible sunshine role to provision one" >&2
  exit 1
fi

curl -fsS -X POST -H "Authorization: Bearer $(cat "$TOKEN_FILE")" "$WIZARD_URL" >/dev/null
