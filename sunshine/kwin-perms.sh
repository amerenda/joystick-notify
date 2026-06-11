#!/bin/bash
# Allows Wayland clients to use gated protocols (e.g. zkde_screencast_unstable_v1 for Sunshine)
# without requiring per-app desktop file permission checks.
export KWIN_WAYLAND_NO_PERMISSION_CHECKS=1
