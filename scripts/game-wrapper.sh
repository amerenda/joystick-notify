#!/usr/bin/env bash
# game-wrapper.sh
# Usage in Steam: game-wrapper.sh %command%

# FOOLPROOF LOGGING - does not depend on libraries
echo "WRAPPER_CALLED_AT=$(date -Is)" >> /tmp/game-wrapper-init.log
echo "ARGS: $*" >> /tmp/game-wrapper-init.log
echo "PREFIX_ENV: ${STEAM_COMPAT_COMMAND_PREFIX:-NOT_SET}" >> /tmp/game-wrapper-init.log

# Source modular libraries
LIB_DIR="/usr/local/lib/joystick-notify"
for lib in "$LIB_DIR"/*.sh; do
    # shellcheck disable=SC1090
    [ -f "$lib" ] && source "$lib"
done

LOG="/tmp/game-wrapper.log"
WRAPPER_DEBUG="${WRAPPER_DEBUG:-false}"

# Integration with central debug logger
debug_wrap() {
    debug "WRAPPER" "$*"
}

# Prevent recursion if the wrapper is called multiple times (global + local)
if [ "${GAMESCOPE_RE_WRAPPED:-0}" = "1" ]; then
    debug_wrap "Already wrapped, executing directly: $*"
    exec "$@"
fi
export GAMESCOPE_RE_WRAPPED=1

# Configuration for resolution
OUT_W="${OUT_W:-3840}"
OUT_H="${OUT_H:-2160}"
GAME_W="${GAME_W:-$OUT_W}"
GAME_H="${GAME_H:-$OUT_H}"

# Ensure log exists if legacy debug is enabled
if [ "$WRAPPER_DEBUG" = "true" ]; then
    : > "$LOG"
    chmod 666 "$LOG" || true
fi

# Get the game name from arguments (usually the last few parts of the command)
GAME_NAME="Unknown"
for arg in "$@"; do
    if [[ "$arg" == *"/steamapps/common/"* ]]; then
        GAME_NAME=$(echo "$arg" | sed -E 's/.*\/steamapps\/common\/([^\/]+).*/\1/')
        break
    fi
done

debug_wrap "Launching game: $GAME_NAME"
debug_wrap "STEAM_COMPAT_COMMAND_PREFIX: ${STEAM_COMPAT_COMMAND_PREFIX:-NOT_SET}"

if is_couch_mode; then
    debug_wrap "Couch Mode active, preparing gamescope command..."
    
    GAMESCOPE_CMD=(
        gamescope
        -W "$OUT_W" -H "$OUT_H"
        -w "$GAME_W" -h "$GAME_H"
        -f -r 60
        -e
        --adaptive-sync
        --rt
        --force-grab-cursor
        --borderless
        --expose-wayland
        --
    )

    debug_wrap "Gamescope prefix: ${GAMESCOPE_CMD[*]}"
    debug_wrap "Final command: ${GAMESCOPE_CMD[@]} $*"
    
    if [ "$DEBUG_MODE" = "true" ] && [ "${DEBUG_WRAPPER:-false}" = "true" ]; then
        exec "${GAMESCOPE_CMD[@]}" "$@" 2>> "$LOG"
    else
        exec "${GAMESCOPE_CMD[@]}" "$@"
    fi
else
    debug_wrap "Couch Mode not active, launching game normally."
    debug_wrap "Final command: $*"
    exec "$@"
fi
