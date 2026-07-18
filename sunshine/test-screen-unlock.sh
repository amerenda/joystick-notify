#!/usr/bin/env bash
# test-screen-unlock.sh — End-to-end verification of the Sunshine lock screen chain.
#
# WHAT THIS TESTS:
#   Section 1 — Config integrity (catches the root cause: missing prep-cmd in apps.json)
#   Section 2 — Script existence and executability
#   Section 3 — couch-mode-screen-unlock.sh unit tests (disable/restore autolock)
#   Section 4 — Active lock screen dismissal
#   Section 5 — Inhibit daemon registers and releases cookie
#   Section 6 — Full prep/undo cycle (simulates Moonlight connect/disconnect)
#
# WHY SECTION 1 IS CRITICAL:
#   global_prep_cmd in sunshine.conf only fires for apps that have a 'cmd' field.
#   Desktop (no cmd) and detached apps never trigger it, so the unlock script
#   never runs. Each app must have an explicit prep-cmd entry. If that test fails,
#   sections 3-6 are meaningless — the scripts will never be called by Sunshine.
#
# Run as the logged-in user with an active KDE/Wayland session.
# Usage: bash test-screen-unlock.sh
# Exit 0 on all pass, 1 if any check fails.

set -uo pipefail

export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$(id -u)/bus}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

APPS_JSON="${HOME}/.config/sunshine/apps.json"
PREP="/usr/local/bin/sunshine-couch-prep.sh"
UNDO="/usr/local/bin/sunshine-couch-undo.sh"
UNLOCK="/usr/local/bin/couch-mode-screen-unlock.sh"
INHIBIT_DAEMON="/usr/local/bin/screen-lock-inhibit-daemon.sh"
INHIBIT_PID_FILE="/tmp/sunshine-screen-inhibit-pid-$(id -u)"
PREP_LOG="/tmp/sunshine-couch-debug.log"

PASS=0 FAIL=0 SKIP=0
HAS_WAYLAND=false
qdbus6 org.freedesktop.ScreenSaver /ScreenSaver org.freedesktop.ScreenSaver.GetActive >/dev/null 2>&1 && HAS_WAYLAND=true

pass()  { echo "  PASS: $*"; PASS=$((PASS+1)); }
fail()  { echo "  FAIL: $*"; FAIL=$((FAIL+1)); }
skip()  { echo "  SKIP: $*"; SKIP=$((SKIP+1)); }

check_eq() {
    local desc="$1" expected="$2" actual="$3"
    if [ "$actual" = "$expected" ]; then
        pass "$desc"
    else
        fail "$desc (expected='$expected' got='$actual')"
    fi
}

autolock_val() { kreadconfig6 --file kscreenlockerrc --group Daemon --key Autolock 2>/dev/null || echo "?"; }
get_active()   { qdbus6 org.freedesktop.ScreenSaver /ScreenSaver org.freedesktop.ScreenSaver.GetActive 2>/dev/null || echo "?"; }
greeter_running() { pgrep -f kscreenlocker_greet >/dev/null 2>&1 && echo yes || echo no; }

echo "=== sunshine-couch-unlock end-to-end test ==="
echo "Date: $(date)"
echo "Wayland session: $HAS_WAYLAND"
echo

# ── Section 1: Config integrity ───────────────────────────────────────────────
# THIS IS THE TEST THAT CATCHES THE ROOT CAUSE.
# If prep-cmd is missing from any app, the unlock script never runs.
# All other tests passing while this fails means NOTHING IS ACTUALLY WIRED UP.
echo "[ 1 ] apps.json: every app must have prep-cmd (root cause gate)"

if [ ! -f "$APPS_JSON" ]; then
    fail "apps.json missing at $APPS_JSON — Sunshine has no app config"
else
    pass "apps.json exists at $APPS_JSON"

    if ! command -v python3 >/dev/null 2>&1; then
        fail "python3 not available — cannot parse apps.json"
    else
        apps_without_prep=$(python3 - "$APPS_JSON" <<'EOF'
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
bad = [a.get('name', '?') for a in data.get('apps', []) if 'prep-cmd' not in a]
print('\n'.join(bad))
EOF
        )

        if [ -z "$apps_without_prep" ]; then
            pass "all apps have prep-cmd"
        else
            while IFS= read -r app_name; do
                fail "app '$app_name' is MISSING prep-cmd — unlock script will NEVER run for this app"
            done <<< "$apps_without_prep"
        fi

        prep_scripts=$(python3 - "$APPS_JSON" <<'EOF'
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
scripts = set()
for a in data.get('apps', []):
    for p in a.get('prep-cmd', []):
        if p.get('do'):
            scripts.add(p['do'])
print('\n'.join(sorted(scripts)))
EOF
        )

        if echo "$prep_scripts" | grep -q "sunshine-couch-prep.sh"; then
            pass "prep-cmd do → sunshine-couch-prep.sh"
        else
            fail "prep-cmd do does not reference sunshine-couch-prep.sh (got: $(echo "$prep_scripts" | tr '\n' ' '))"
        fi

        undo_scripts=$(python3 - "$APPS_JSON" <<'EOF'
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
scripts = set()
for a in data.get('apps', []):
    for p in a.get('prep-cmd', []):
        if p.get('undo'):
            scripts.add(p['undo'])
print('\n'.join(sorted(scripts)))
EOF
        )

        if echo "$undo_scripts" | grep -q "sunshine-couch-undo.sh"; then
            pass "prep-cmd undo → sunshine-couch-undo.sh"
        else
            fail "prep-cmd undo does not reference sunshine-couch-undo.sh (got: $(echo "$undo_scripts" | tr '\n' ' '))"
        fi
    fi
fi

# ── Section 2: Script existence ───────────────────────────────────────────────
echo
echo "[ 2 ] all required scripts exist and are executable"

for script in "$PREP" "$UNDO" "$UNLOCK" "$INHIBIT_DAEMON"; do
    if [ -x "$script" ]; then
        pass "$script"
    elif [ -f "$script" ]; then
        fail "$script exists but is not executable (+x missing)"
    else
        fail "$script is MISSING — Sunshine prep-cmd will fail silently at session connect"
    fi
done

# ── Section 3: couch-mode-screen-unlock.sh unit tests ─────────────────────────
echo
echo "[ 3 ] unlock script: start disables Autolock, stop restores it"

original_autolock=$(autolock_val)
"$UNLOCK" start
check_eq "Autolock=false after start" "false" "$(autolock_val)"
"$UNLOCK" stop
check_eq "Autolock restored to original after stop" "$original_autolock" "$(autolock_val)"

# ── Section 4: Active lock screen dismissal ───────────────────────────────────
echo
echo "[ 4 ] start dismisses an active lock screen"

if [ "$HAS_WAYLAND" = "false" ]; then
    skip "no Wayland session (GetActive unavailable)"
else
    qdbus6 org.freedesktop.ScreenSaver /ScreenSaver org.freedesktop.ScreenSaver.Lock 2>/dev/null || true
    sleep 1.5
    check_eq "GetActive=true after Lock()" "true" "$(get_active)"
    check_eq "greeter running after Lock()" "yes" "$(greeter_running)"

    "$UNLOCK" start
    sleep 1.5
    check_eq "GetActive=false after unlock" "false" "$(get_active)"
    check_eq "greeter gone after unlock" "no" "$(greeter_running)"
    check_eq "Autolock=false after unlock" "false" "$(autolock_val)"

    "$UNLOCK" stop
    check_eq "Autolock restored after stop" "$original_autolock" "$(autolock_val)"
fi

# ── Section 5: Inhibit daemon ─────────────────────────────────────────────────
echo
echo "[ 5 ] inhibit daemon: holds ScreenSaver.Inhibit() for session lifetime"

# Clean up any leftover daemon
if [ -f "$INHIBIT_PID_FILE" ]; then
    kill "$(cat "$INHIBIT_PID_FILE" 2>/dev/null)" 2>/dev/null || true
    rm -f "$INHIBIT_PID_FILE"
fi

if [ "$HAS_WAYLAND" = "false" ]; then
    skip "no Wayland session (inhibit daemon needs session bus)"
else
    "$INHIBIT_DAEMON" "test" "unit test" &
    daemon_pid=$!
    sleep 0.8

    if kill -0 "$daemon_pid" 2>/dev/null; then
        pass "inhibit daemon still running 0.8s after start (Inhibit() succeeded)"
    else
        fail "inhibit daemon exited immediately — Inhibit() call failed or daemon crashed"
    fi

    kill "$daemon_pid" 2>/dev/null || true
    wait "$daemon_pid" 2>/dev/null || true
    sleep 0.3

    if kill -0 "$daemon_pid" 2>/dev/null; then
        fail "inhibit daemon still alive after kill — cleanup may not have run"
    else
        pass "inhibit daemon exited cleanly after SIGTERM"
    fi
fi

# ── Section 6: Full prep/undo cycle ──────────────────────────────────────────
# This is the test that mirrors what Sunshine does on session connect/disconnect.
echo
echo "[ 6 ] full prep/undo cycle (mirrors Moonlight connect → disconnect)"

# Reset to clean state
"$UNLOCK" stop 2>/dev/null || true
if [ -f "$INHIBIT_PID_FILE" ]; then
    kill "$(cat "$INHIBIT_PID_FILE" 2>/dev/null)" 2>/dev/null || true
    rm -f "$INHIBIT_PID_FILE"
fi
rm -f "$PREP_LOG"

if [ "$HAS_WAYLAND" = "false" ]; then
    skip "no Wayland session — skipping full cycle test"
else
    # Lock the screen to simulate the real scenario: user connects while screen is locked
    qdbus6 org.freedesktop.ScreenSaver /ScreenSaver org.freedesktop.ScreenSaver.Lock 2>/dev/null || true
    sleep 1.5
    echo "      Screen locked. Running prep (simulates Moonlight connect)..."

    "$PREP" >/dev/null 2>&1 || true
    sleep 1.5

    check_eq "prep: GetActive=false (screen unlocked)" "false" "$(get_active)"
    check_eq "prep: greeter gone" "no" "$(greeter_running)"
    check_eq "prep: Autolock=false (no auto-lock during stream)" "false" "$(autolock_val)"

    if [ -f "$INHIBIT_PID_FILE" ]; then
        inhibit_pid=$(cat "$INHIBIT_PID_FILE")
        if kill -0 "$inhibit_pid" 2>/dev/null; then
            pass "prep: inhibit daemon running (pid=$inhibit_pid) — screen cannot auto-lock"
        else
            fail "prep: inhibit PID file exists but process is dead — screen may auto-lock during streaming"
        fi
    else
        fail "prep: no inhibit daemon PID file — screen WILL auto-lock during streaming (no Inhibit cookie held)"
    fi

    if [ -f "$PREP_LOG" ] && grep -q "sunshine-couch-prep.sh" "$PREP_LOG" 2>/dev/null; then
        pass "prep: log entry written to $PREP_LOG"
    else
        fail "prep: nothing in $PREP_LOG — prep script may not have actually run"
    fi

    echo "      Running undo (simulates Moonlight disconnect)..."
    "$UNDO" >/dev/null 2>&1 || true
    sleep 0.5

    check_eq "undo: Autolock restored" "$original_autolock" "$(autolock_val)"

    if [ -f "$INHIBIT_PID_FILE" ]; then
        stale_pid=$(cat "$INHIBIT_PID_FILE" 2>/dev/null || true)
        if kill -0 "$stale_pid" 2>/dev/null; then
            fail "undo: inhibit daemon still running (pid=$stale_pid) — lock inhibitor not released"
        else
            fail "undo: stale PID file exists for dead process — cleanup incomplete"
        fi
    else
        pass "undo: inhibit daemon cleaned up"
    fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo
total=$((PASS + FAIL + SKIP))
if [ "$FAIL" -eq 0 ]; then
    echo "=== ALL $PASS/$total PASSED (${SKIP} skipped) ==="
    exit 0
else
    echo "=== $FAIL FAILED, $PASS passed, $SKIP skipped (${total} total) ==="
    echo
    echo "NOTE: Section 1 failure means Sunshine will NEVER call the unlock script."
    echo "      Passing sections 3-6 while section 1 fails is a false positive."
    exit 1
fi
