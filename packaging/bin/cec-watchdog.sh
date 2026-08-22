#!/usr/bin/env bash
# cec-watchdog.sh - periodic + on-demand health check: if /dev/cec0 has gone
# missing (pulse8-cec line discipline died without a USB event to
# re-trigger pulse8-cec-autoattach.rules, or the kernel driver wedged),
# reattach and re-run the escalated USB-bus-reset fixup. Direct port of
# v1's scripts/cec-watchdog.sh — this is genuinely root/system-level (USB
# bus reset, systemd system-unit restarts), so it stays a small bash
# script installed alongside the root-owned systemd units rather than
# being pulled into the Python daemon (see
# plans/joystick-notify-v2.md decision #3 on the root carve-out).
set -uo pipefail
LOG_TAG="cec-watchdog"
log() { logger -t "$LOG_TAG" -- "$*"; }

[ -e /dev/cec0 ] && exit 0

log "cec0 missing, restarting pulse8-cec-attach@ instance(s) + cec-fixup.service"
units="$(systemctl list-units --all --plain --no-legend 'pulse8-cec-attach@*' 2>/dev/null | awk '{print $1}')"
if [ -z "$units" ]; then
    log "no pulse8-cec-attach@ instance found (adapter not enumerated?)"
else
    for u in $units; do
        log "restarting $u"
        systemctl restart "$u" || log "restart failed: $u"
    done
fi
sleep 3
[ -e /dev/cec0 ] && { log "cec0 recovered after reattach"; exit 0; }

log "cec0 still missing after reattach, running cec-fixup.service (USB bus reset)"
systemctl start --no-block cec-fixup.service
