#!/usr/bin/env bash
# sunshine-unlock-prep.sh — Sunshine global_prep_cmd "do" hook, runs once
# per stream start (any app). Undo counterpart: sunshine-unlock-undo.sh.
#
# Deliberately narrow: a Sunshine stream (often to a Steam Deck or other
# remote client, not necessarily anyone sitting at the couch/TV) should
# get the screen unlocked and kept awake for the stream -- nothing more.
# It must NOT trigger the full couch-mode transition (CEC TV/receiver
# wake, physical display output switch, audio switch, cursor hide,
# launcher) the way a controller connecting on the couch does -- Sunshine
# captures the desktop via KWin regardless of which output is active, and
# the Moonlight client renders its own audio, so none of that hardware
# state has anything to do with a remote stream. See
# src/joystick_notify/wizard/server.py's api_screen_unlock and
# actions/screen_lock.py for what actually happens on the other end --
# the exact same function daemon.py's own couch-mode hook calls, just
# without going through the state machine's mode transition at all.
#
# Requires an API token installed via `jn-daemon --install-api-token`
# (provisioned by ansible-playbooks' roles/sunshine, value from BWS) —
# see wizard/auth.py's install_api_token() for why this is a purpose-
# built token, not the wizard's own admin login password.
set -euo pipefail

TOKEN_FILE="${HOME}/.config/joystick-notify/sunshine-api-token"
# Must match config.toml's [wizard] port (default 8642) on this host.
WIZARD_URL="http://127.0.0.1:8642/api/screen/unlock"

if [ ! -r "$TOKEN_FILE" ]; then
  echo "sunshine-unlock-prep: no API token at $TOKEN_FILE -- run the ansible sunshine role to provision one" >&2
  exit 1
fi

curl -fsS -X POST -H "Authorization: Bearer $(cat "$TOKEN_FILE")" "$WIZARD_URL" >/dev/null
