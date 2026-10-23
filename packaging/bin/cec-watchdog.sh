#!/usr/bin/env bash
# cec-watchdog.sh - periodic + on-demand health check: if /dev/cec0 is
# present but systemd no longer tracks it (see below), replay its udev add;
# if /dev/cec0 has gone missing (pulse8-cec line discipline died without a
# USB event to re-trigger pulse8-cec-autoattach.rules, or the kernel driver
# wedged), reattach and re-run the escalated USB-bus-reset fixup. Direct port of
# v1's scripts/cec-watchdog.sh — this is genuinely root/system-level (USB
# bus reset, systemd system-unit restarts), so it stays a small bash
# script installed alongside the root-owned systemd units rather than
# being pulled into the Python daemon (see
# plans/joystick-notify-v2.md decision #3 on the root carve-out).
set -uo pipefail
LOG_TAG="cec-watchdog"
log() { logger -t "$LOG_TAG" -- "$*"; }

# /dev/cec0 present but dev-cec0.device inactive: a udev "change" event
# stripped the systemd tag (pre-fix cec-configure-autostart.rules matched
# ACTION=="add" only), so BindsTo stopped cec0-configure.service and nothing
# restarts it -- the adapter sits at phys f.f.f.f with no logical address,
# the TV ignores our Set Stream Path, and whatever else is on the bus (the
# Shield) keeps the input. Replaying "add" re-tags the device; systemd then
# sees it become active and starts cec0-configure via SYSTEMD_WANTS.
if [ -e /dev/cec0 ]; then
    systemctl is-active --quiet dev-cec0.device && exit 0
    log "cec0 present but dev-cec0.device inactive (cec0-configure not running), replaying udev add"
    udevadm trigger --action=add --subsystem-match=cec
    udevadm settle --timeout=5 || true
    if systemctl is-active --quiet dev-cec0.device; then
        log "dev-cec0.device active again"
    else
        log "dev-cec0.device still inactive after replaying udev add"
    fi
    exit 0
fi

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
