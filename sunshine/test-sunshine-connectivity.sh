#!/usr/bin/env bash
# test-sunshine-connectivity.sh — Regression guard for the Sunshine/Moonlight
# outage chain diagnosed and fixed on 2026-07-19/20 (see ansible-playbooks
# roles/sunshine and memory project_sunshine_moonlight_archlinux.md for the
# full incident writeup).
#
# WHAT THIS TESTS (each section gates on a real root cause hit that day):
#   Section 1 — KWin screencast permission (encoder/display init fatal error)
#   Section 2 — Sunshine desktop-file override (KWin binary->desktop matching)
#   Section 3 — Live session env consistency (WAYLAND_DISPLAY/XAUTHORITY/DISPLAY
#               vs the actually-running KWin process — the post-`--replace`
#               desync that broke Steam and looked like a random RTSP timeout)
#   Section 4 — sunshine.conf integrity (csrf_allowed_origins, origin_web_ui_allowed)
#   Section 5 — UFW rules for every Sunshine port
#   Section 6 — Service health (running, no fatal encoder/capture errors, no
#               recent crash-loop)
#   Section 7 — Live API check (serverinfo reachable, reports a sane state)
#
# WHY THIS EXISTS:
#   Every one of these was individually silent — Sunshine would start, the web
#   UI would load, pairing would say "Success!" — while the actual session
#   never worked. None of it surfaced as an obvious error. This script exists
#   so the next time any of these regress (e.g. after a CachyOS reinstall that
#   recreates the box from ansible but misses anything NOT captured in a role),
#   it's caught in one run instead of a multi-hour live debugging session.
#
# Run as the logged-in user with an active KDE/Wayland session on the
# archlinux/CachyOS box itself (not over SSH from another host, though SSH TO
# this host and running it there is fine).
# Usage: bash test-sunshine-connectivity.sh
# Exit 0 on all pass (SKIPs allowed), 1 if any check fails.

set -uo pipefail

export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$(id -u)/bus}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

SUNSHINE_CONF="${HOME}/.config/sunshine/sunshine.conf"
SUNSHINE_STATE="${HOME}/.config/sunshine/sunshine_state.json"
KWIN_PERMS_SCRIPT="${HOME}/.config/plasma-workspace/env/kwin-perms.sh"
DESKTOP_FILE="${HOME}/.local/share/applications/dev.lizardbyte.app.Sunshine.desktop"
SUNSHINE_SERVICE="app-dev.lizardbyte.app.Sunshine.service"

PASS=0 FAIL=0 SKIP=0

pass() { echo "  PASS: $*"; PASS=$((PASS+1)); }
fail() { echo "  FAIL: $*"; FAIL=$((FAIL+1)); }
skip() { echo "  SKIP: $*"; SKIP=$((SKIP+1)); }

echo "=== sunshine-connectivity regression test ==="
echo "Date: $(date)"
echo "Host: $(hostname)"
echo

# ── Section 1: KWin screencast permission ─────────────────────────────────────
# Without this, `capture = kwin` fails with "Couldn't find any working encoder
# matching [vaapi]" / "Unable to find display or encoder during startup" even
# though vainfo and the DRM connector are both fine — KWin just never
# advertises zkde_screencast_unstable_v1 to Wayland clients.
echo "[ 1 ] KWin screencast permission (kwin-perms.sh)"

if [ ! -f "$KWIN_PERMS_SCRIPT" ]; then
    fail "$KWIN_PERMS_SCRIPT missing — KWin will not advertise zkde_screencast_unstable_v1"
else
    pass "$KWIN_PERMS_SCRIPT exists"
    if grep -q '^export KWIN_WAYLAND_NO_PERMISSION_CHECKS=1' "$KWIN_PERMS_SCRIPT"; then
        pass "kwin-perms.sh sets KWIN_WAYLAND_NO_PERMISSION_CHECKS=1"
    else
        fail "kwin-perms.sh does not set KWIN_WAYLAND_NO_PERMISSION_CHECKS=1"
    fi
fi

env_kwin_perm=$(systemctl --user show-environment 2>/dev/null | grep '^KWIN_WAYLAND_NO_PERMISSION_CHECKS=' | cut -d= -f2-)
if [ "$env_kwin_perm" = "1" ]; then
    pass "KWIN_WAYLAND_NO_PERMISSION_CHECKS=1 is active in the session (systemd --user environment)"
else
    fail "KWIN_WAYLAND_NO_PERMISSION_CHECKS is NOT set in the session's systemd --user environment — kwin-perms.sh only takes effect on the NEXT full session start (login/reboot); a live KWin --replace does not source it"
fi

echo

# ── Section 2: Sunshine desktop-file override ─────────────────────────────────
echo "[ 2 ] Sunshine desktop-file override (KWin binary->desktop-file matching)"

if [ ! -f "$DESKTOP_FILE" ]; then
    fail "$DESKTOP_FILE missing"
else
    pass "$DESKTOP_FILE exists"
    if grep -q '^Exec=/usr/bin/sunshine$' "$DESKTOP_FILE"; then
        pass "Exec=/usr/bin/sunshine set correctly"
    else
        fail "Exec= line missing or incorrect in $DESKTOP_FILE"
    fi
    if grep -q '^X-KDE-Wayland-Interfaces=zkde_screencast_unstable_v1$' "$DESKTOP_FILE"; then
        pass "X-KDE-Wayland-Interfaces set correctly"
    else
        fail "X-KDE-Wayland-Interfaces missing or incorrect in $DESKTOP_FILE"
    fi
fi

echo

# ── Section 3: Live session env consistency ────────────────────────────────────
# The 2026-07-19 incident: a live `kwin_wayland_wrapper --replace` (used to
# avoid a full logout) left the systemd --user manager's WAYLAND_DISPLAY and
# XAUTHORITY pointing at the OLD dead KWin process. Sunshine's own env stayed
# correct (it inherits at its OWN start), but anything it launched (Steam via
# apps.json prep-cmd) inherited the stale values and failed with
# "Unable to open display" — which surfaced client-side as a bare RTSP
# handshake timeout, nothing that pointed at the actual cause.
echo "[ 3 ] Live session env vs actual running KWin process"

KWIN_PID_LINE=$(pgrep -a -f '^/usr/bin/kwin_wayland --' | head -1 || true)
if [ -z "$KWIN_PID_LINE" ]; then
    skip "no running kwin_wayland process found — not in a graphical session?"
else
    live_socket=$(echo "$KWIN_PID_LINE" | grep -oP '(?<=--socket )\S+' || echo "")
    live_xauth=$(echo "$KWIN_PID_LINE" | grep -oP '(?<=--xwayland-xauthority )\S+' || echo "")
    live_display=$(echo "$KWIN_PID_LINE" | grep -oP '(?<=--xwayland-display )\S+' || echo "")

    env_wayland=$(systemctl --user show-environment 2>/dev/null | grep '^WAYLAND_DISPLAY=' | cut -d= -f2-)
    env_xauth=$(systemctl --user show-environment 2>/dev/null | grep '^XAUTHORITY=' | cut -d= -f2-)
    env_display=$(systemctl --user show-environment 2>/dev/null | grep '^DISPLAY=' | cut -d= -f2-)

    if [ -n "$live_socket" ] && [ "$env_wayland" = "$live_socket" ]; then
        pass "WAYLAND_DISPLAY matches live KWin socket ($live_socket)"
    else
        fail "WAYLAND_DISPLAY mismatch: systemd env='$env_wayland' live KWin socket='$live_socket'"
    fi

    if [ -n "$live_xauth" ] && [ "$env_xauth" = "$live_xauth" ] && [ -e "$live_xauth" ]; then
        pass "XAUTHORITY matches live KWin xwayland-xauthority ($live_xauth) and file exists"
    else
        fail "XAUTHORITY mismatch or missing file: systemd env='$env_xauth' live KWin xauth='$live_xauth' (exists=$( [ -e "$live_xauth" ] && echo yes || echo no ))"
    fi

    if [ -n "$live_display" ] && [ "$env_display" = "$live_display" ]; then
        pass "DISPLAY matches live KWin xwayland-display ($live_display)"
    else
        fail "DISPLAY mismatch: systemd env='$env_display' live KWin xwayland-display='$live_display'"
    fi
fi

echo

# ── Section 4: sunshine.conf integrity ─────────────────────────────────────────
echo "[ 4 ] sunshine.conf integrity"

if [ ! -f "$SUNSHINE_CONF" ]; then
    fail "$SUNSHINE_CONF missing"
else
    pass "$SUNSHINE_CONF exists"

    if grep -q '^capture = kwin$' "$SUNSHINE_CONF"; then
        pass "capture = kwin"
    else
        fail "capture is not set to kwin"
    fi

    if grep -q '^csrf_allowed_origins' "$SUNSHINE_CONF"; then
        pass "csrf_allowed_origins is set (needed for the web UI reached via any non-default origin, e.g. the Tailscale subnet route)"
    else
        fail "csrf_allowed_origins missing — remote web UI pairing will fail with a CSRF protection error"
    fi

    if grep -q '^origin_web_ui_allowed = wan$' "$SUNSHINE_CONF"; then
        pass "origin_web_ui_allowed = wan"
    else
        fail "origin_web_ui_allowed is not set to wan — PIN pairing from a non-LAN origin (e.g. via the Tailscale subnet router) will report a bogus \"Success\" but never actually complete (LizardByte/Sunshine#3944 interacts with this setting)"
    fi
fi

echo

# ── Section 5: UFW rules for every Sunshine port ───────────────────────────────
echo "[ 5 ] UFW rules"

if ! command -v ufw >/dev/null 2>&1; then
    skip "ufw not installed"
else
    ufw_out=$(sudo ufw status 2>/dev/null)
    for p in 47984/tcp 47989/tcp 47990/tcp 48010/tcp 47998/udp 47999/udp 48000/udp 48002/udp 48010/udp; do
        if echo "$ufw_out" | grep -qE "^${p}[[:space:]]+ALLOW"; then
            pass "$p allowed"
        else
            fail "$p is NOT allowed in ufw"
        fi
    done
fi

echo

# ── Section 6: Service health ──────────────────────────────────────────────────
echo "[ 6 ] Sunshine service health"

if systemctl --user is-active --quiet "$SUNSHINE_SERVICE"; then
    pass "$SUNSHINE_SERVICE is active"
else
    fail "$SUNSHINE_SERVICE is not active"
fi

restart_count=$(systemctl --user show "$SUNSHINE_SERVICE" -p NRestarts --value 2>/dev/null || echo "?")
if [ "$restart_count" = "0" ]; then
    pass "0 restarts since last (re)start"
else
    fail "service has restarted $restart_count time(s) since last (re)start — check for a crash loop"
fi

recent_fatal=$(journalctl --user -u "$SUNSHINE_SERVICE" --since '10 minutes ago' --no-pager 2>/dev/null | grep -cE 'Fatal:|Couldn.t find any working encoder|Unable to find display or encoder' || true)
if [ "${recent_fatal:-0}" -eq 0 ]; then
    pass "no fatal encoder/display errors in the last 10 minutes"
else
    fail "$recent_fatal fatal encoder/display error(s) in the last 10 minutes — check: journalctl --user -u $SUNSHINE_SERVICE --since '10 minutes ago'"
fi

echo

# ── Section 7: Live API check ──────────────────────────────────────────────────
echo "[ 7 ] Live serverinfo API"

if ! command -v curl >/dev/null 2>&1; then
    skip "curl not available"
else
    serverinfo=$(curl -s --max-time 5 'http://localhost:47989/serverinfo?uniqueid=0000000000000000' 2>/dev/null || true)
    if [ -z "$serverinfo" ]; then
        fail "serverinfo endpoint did not respond on :47989"
    else
        pass "serverinfo endpoint responded"
        state=$(echo "$serverinfo" | grep -oP '(?<=<state>)[^<]+' || echo "")
        case "$state" in
            SUNSHINE_SERVER_FREE|SUNSHINE_SERVER_BUSY)
                pass "server state is sane ($state)"
                ;;
            *)
                fail "unexpected server state: '$state'"
                ;;
        esac
    fi
fi

if [ -f "$SUNSHINE_STATE" ]; then
    paired_count=$(python3 -c "import json; print(len(json.load(open('$SUNSHINE_STATE'))['root'].get('named_devices', [])))" 2>/dev/null || echo "?")
    if [ "$paired_count" != "?" ] && [ "$paired_count" -gt 0 ]; then
        pass "$paired_count paired device(s) recorded in sunshine_state.json"
    else
        fail "no paired devices recorded in sunshine_state.json (this is expected on a totally fresh install, but not otherwise)"
    fi
else
    skip "sunshine_state.json not found"
fi

echo
echo "=== Summary: $PASS passed, $FAIL failed, $SKIP skipped ==="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
