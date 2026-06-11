#!/bin/bash
# Keep ~/.Xauthority in sync with the KWin/Xwayland per-session auth file
xauth_file=
if [ -n "" ]; then
    export XAUTHORITY=""
    xauth -f "" extract - :1 2>/dev/null | xauth -f ~/.Xauthority merge - 2>/dev/null || true
fi
