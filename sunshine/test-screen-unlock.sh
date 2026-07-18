#!/usr/bin/env bash
# Test that couch-mode-screen-unlock.sh correctly disables autolock and
# dismisses an active KDE lock screen.
#
# Run as the logged-in user (alex) with an active KDE/Wayland session.
# Usage: bash test-screen-unlock.sh
#
# Exit 0 on all pass, 1 if any check fails.

set -uo pipefail

export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$(id -u)/bus}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

UNLOCK=/usr/local/bin/couch-mode-screen-unlock.sh
PASS=0 FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS+1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }

check_eq() {
    local desc="$1" expected="$2" actual="$3"
    if [ "$actual" = "$expected" ]; then pass "$desc"; else fail "$desc (expected='$expected' got='$actual')"; fi
}

autolock_val() {
    kreadconfig6 --file kscreenlockerrc --group Daemon --key Autolock 2>/dev/null || echo "?"
}

greeter_running() {
    # Use -f on the full binary path to avoid the 15-char comm truncation.
    # The script itself does not contain "kscreenlocker_greet" in its argv,
    # so pgrep -f won't false-match the calling shell.
    pgrep -f kscreenlocker_greet >/dev/null 2>&1 && echo yes || echo no
}

echo "=== couch-mode-screen-unlock.sh test ==="
echo "Script: $UNLOCK"
echo

# --- Test 1: start disables Autolock -----------------------------------------
echo "[ 1 ] start disables Autolock"
"$UNLOCK" start
check_eq "Autolock=false after start" "false" "$(autolock_val)"

# --- Test 2: stop restores Autolock ------------------------------------------
echo
echo "[ 2 ] stop restores Autolock"
"$UNLOCK" stop
check_eq "Autolock=true after stop" "true" "$(autolock_val)"

# --- Test 3: start dismisses an active lock screen ---------------------------
echo
echo "[ 3 ] lock screen then dismiss via start"
echo "      Locking screen via DBus..."
qdbus6 org.freedesktop.ScreenSaver /ScreenSaver org.freedesktop.ScreenSaver.Lock 2>/dev/null || {
    echo "  SKIP: could not lock screen via DBus (no active Wayland session?)"
    PASS=$((PASS+1))
}

sleep 1.5
check_eq "greeter running after Lock()" "yes" "$(greeter_running)"

echo "      Running '$UNLOCK start'..."
"$UNLOCK" start
sleep 1.5

check_eq "greeter gone after start" "no" "$(greeter_running)"
check_eq "Autolock=false after start" "false" "$(autolock_val)"

# Restore state
"$UNLOCK" stop

# --- Test 4: start is idempotent when already unlocked -----------------------
echo
echo "[ 4 ] start is idempotent when screen is already unlocked"
check_eq "no greeter before start" "no" "$(greeter_running)"
"$UNLOCK" start
sleep 0.5
check_eq "still no greeter after start (no lock active)" "no" "$(greeter_running)"
check_eq "Autolock=false after idempotent start" "false" "$(autolock_val)"
"$UNLOCK" stop

# --- Summary -----------------------------------------------------------------
echo
if [ "$FAIL" -eq 0 ]; then
    echo "=== ALL $PASS PASSED ==="
    exit 0
else
    echo "=== $PASS passed, $FAIL FAILED ==="
    exit 1
fi
