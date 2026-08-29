# joystick-notify

Controller-triggered couch/desk mode switcher: plug in a gamepad, the
display switches to the couch/TV output, audio follows, the TV powers on
and switches input via HDMI-CEC (if you have CEC hardware), and your game
launcher starts. Unplug (or exit the game), and it switches back.

**This is a from-scratch rewrite (v2) of the original bash implementation.**
See `plans/joystick-notify-v2.md` in this repo's history (or ask in-session)
for the full design rationale — in short: v1 was a pile of independently
sourced shell scripts coordinating through `/tmp` files and `&`-backgrounded
subshells with no shared runtime, which made a whole class of bugs
(controller-flap races, detached jobs with no cancellation, silently
swallowed failures) structurally hard to fix one at a time. v2 is a single
Python `asyncio` daemon where debounce, state transitions, and health
reporting are each handled in exactly one place.

## Status

Early prototype. Core (`debounce` → `state_machine` → CEC/display/audio/
launch actions), the `Health` registry, config storage, and an embedded
setup wizard (with forced first-run auth) are implemented and covered by
~110 unit/integration tests — see `tests/`. Not yet dogfooded against real
hardware; see the migration plan for the staged rollout this repo's `main`
branch (still the live v1 bash implementation) will follow before this
replaces it.

## Requirements

- Python 3.11+
- `kscreen-doctor` (KDE Plasma / KWin, Wayland) for display switching
- `pactl` (PipeWire/PulseAudio) for audio routing
- `cec-ctl` (v4l-utils) for HDMI-CEC — optional; skip entirely if you have
  no CEC hardware, display switching and controller-triggered launch work
  without it
- `PyQt6` — optional, only for the tray icon

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```

Run the self-test (checks required binaries, CEC reachability, config
validity — the same checks the running daemon does at startup):

```bash
.venv/bin/jn-daemon --doctor
```

Run the daemon (starts the embedded setup wizard on `127.0.0.1:8642` by
default):

```bash
.venv/bin/jn-daemon
```

## Packaging

`packaging/PKGBUILD` is the day-to-day install/test method during
development (`makepkg -p packaging/PKGBUILD -si` from the repo root).
After install, run `sudo jn-setup-root-carveout.sh` once (root-level CEC
units + a narrowly-scoped NOPASSWD sudoers rule — see that script's header
for why this isn't a pacman scriptlet), then
`systemctl --user enable --now joystick-notify.service`.

## Configuration

Nothing is hand-edited. On first run, the embedded wizard
(`http://127.0.0.1:8642/`) forces you to set an admin password, then walks
through auto-detected displays, audio sinks, controllers, and CEC hardware
with a picker UI — no raw device strings, no env vars. Config lives at
`~/.config/joystick-notify/config.toml`.
