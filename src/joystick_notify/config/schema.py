"""Typed config model — the single source of truth read/written by both the
daemon and the wizard through `store.py`, replacing v1's `config-env.sh`
pile of ad hoc environment variables.

Defaults are intentionally empty/inert (no display ports, CEC disabled) so
a freshly-installed config represents "nothing configured yet" rather than
silently assuming Alex's specific hardware — the wizard is what populates
real values via auto-detection + a picker, per
plans/joystick-notify-v2.md's "Display and audio configuration" section.
"""
from __future__ import annotations

from dataclasses import dataclass, field

CONFIG_SCHEMA_VERSION = 1


@dataclass
class DisplayConfig:
    desk_port: str = ""
    couch_port: str = ""
    desk_mode: str = ""
    couch_mode: str = ""


@dataclass
class AudioConfig:
    desk_sink: str = ""
    couch_sink: str = ""


@dataclass
class CecConfig:
    enabled: bool = False
    adapter: str = ""  # e.g. /dev/cec0; empty = auto-discover across /dev/cec*
    # Empty = use the adapter's auto-discovered physical address (the common
    # case: CEC rides the same cable as video). Non-empty = the guided-picker
    # override for setups like Alex's, where the CEC dongle is on a separate
    # physical path from the video signal — see the plan's CEC detection
    # strategy section for why this one value is genuinely irreducible.
    active_source_phys_addr: str = ""
    wake_delay_s: float = 0.0
    power_off_on_teardown: bool = True
    active_source_retries: int = 2
    active_source_retry_delay_s: float = 4.0
    # Logical addresses (0 = TV, 5 = Audio System/receiver) to standby+verify
    # on teardown. Auto-populated by the wizard's topology scan, not
    # hand-typed (generalizes v1's hardcoded CEC_STANDBY_TARGETS).
    standby_targets: list[int] = field(default_factory=lambda: [0])
    # 3 attempts * 2.0s (the old defaults, ~6-8s total) turned out too
    # short: confirmed via live testing 2026-08-21 that a real TV+receiver
    # both correctly report pwr-state=standby when queried directly, but
    # the daemon's own verify window gave up first every time, logging a
    # false "unconfirmed, may still be on" — the devices just take longer
    # than 6-8s to actually finish powering down after ACK'ing the CEC
    # standby command. 5 * 3.0s (~15s) gives real hardware room to finish.
    standby_verify_attempts: int = 5
    standby_verify_delay_s: float = 3.0


@dataclass
class TimingConfig:
    disconnect_grace_s: float = 30.0
    launch_startup_grace_s: float = 10.0
    poll_interval_s: float = 2.0
    debounce_default_ms: int = 300
    # Per-device-class overrides — e.g. a Steam Puck receiver and an 8BitDo
    # dongle don't necessarily bounce identically. Keyed by profile id from
    # devices/profiles.py.
    debounce_per_class_ms: dict[str, int] = field(default_factory=dict)


@dataclass
class IdleConfig:
    # If the controller disconnects while a launched game is still
    # running, wait for a reconnect instead of tearing down to desk after
    # the usual disconnect_grace_s -- disable to restore the simpler "any
    # disconnect tears down to desk" behavior regardless of whether a game
    # is running (e.g. for a pure display/audio-switching setup, or anyone
    # who'd rather not leave a game running unattended).
    wait_for_game: bool = True
    # Once the owner has been absent this long (with the game still
    # running), engage the screensaver and put the TV into CEC standby
    # (still gated on cec.power_off_on_teardown) -- while staying in
    # couch mode, so a reconnect resumes instantly instead of redoing the
    # whole desk->couch activation.
    screensaver_enabled: bool = True
    idle_after_s: float = 120.0


@dataclass
class ActionConfig:
    # Launcher preset id (from actions/launchers.py) or a custom shell
    # command -- for a user-defined CustomCommand, this holds its `command`
    # string directly (see CustomCommand below), not a name/reference, so
    # launchers.py needs no lookup step to resolve it.
    run: str = ""
    # Sunshine-style paired teardown command: an arbitrary shell command
    # run when switching back to desk, mirroring `run`'s launch-on-connect
    # role but for the other direction -- not a fixed on/off switch, full
    # user control over what "tear down" means for whatever `run` starts.
    # Empty is a real, valid choice (leave the launched process running
    # across the desk<->couch cycle), NOT "use some hardcoded default" --
    # the one exception is the steam-bigpicture preset specifically, which
    # still gets a sensible built-in `steam -shutdown` when this is blank
    # (see launchers.exit_launched()), since we know exactly what "nicely"
    # means for it and typing that out shouldn't be required busywork. Any
    # non-empty value here always wins over that built-in, for any preset
    # or custom command.
    teardown_command: str = ""


@dataclass
class CustomCommand:
    # A user-defined, named entry in the "Launch on connect" picker --
    # `name` is a wizard-UI-only label; `command` is what actually gets
    # run (same shell-command shape as a hand-typed ActionConfig.run).
    name: str = ""
    command: str = ""


@dataclass
class ScreenLockConfig:
    # A real security tradeoff, not a cosmetic feature: bypassing the lock
    # screen makes physical access to the machine equivalent to being
    # logged in. Explicit opt-in only — see actions/screen_lock.py's
    # module docstring. Default False, same treatment CEC gets.
    enabled: bool = False
    # Also hold a ScreenSaver.Inhibit() D-Bus cookie for the whole couch
    # session, on top of disabling the config-based autolock -- the robust
    # layer for cases where the config-based disable alone doesn't hold.
    hold_inhibit: bool = True


@dataclass
class CursorConfig:
    # Off by default, matching screen_lock's explicit-opt-in treatment --
    # this depends on the "invisible" Xcursor theme already existing on
    # the host (ansible-playbooks roles/mouse-hide), so a fresh/
    # undeployed host must not silently no-op switching to a theme that
    # was never installed. See actions/cursor.py's module docstring.
    enabled: bool = False
    hide_theme: str = "invisible"
    # Empty = unknown/don't care what the normal theme is -- activate_desk
    # then leaves the current cursor theme alone rather than guessing at a
    # name that might not exist (see actions/cursor.py).
    normal_theme: str = ""


@dataclass
class ShutdownConfig:
    # Off by default, same explicit-opt-in treatment as screen_lock/cursor
    # -- unlike those, a bug here affects real system shutdown behavior
    # (a held inhibitor lock, even bounded, is real user-visible impact
    # every time the machine powers off), not just this daemon's own
    # session state, so a fresh/undeployed host must never silently start
    # intercepting shutdown until this is deliberately turned on. See
    # shutdown_watcher.py's module docstring.
    enabled: bool = False
    # Leaves a margin under logind's InhibitDelayMaxSec (ansible-managed,
    # see roles/joystick-notify's logind.conf.d drop-in) so the inhibitor
    # lock is always released before logind would force it anyway.
    teardown_timeout_s: float = 13.0


@dataclass
class ShortcutConfig:
    # On by default -- unlike screen_lock, this isn't a security tradeoff,
    # it's a pure safety net: always having a way back to desk regardless
    # of what Steam/the game is doing is a good default, not something
    # that needs opt-in.
    exit_couch_enabled: bool = True
    # evdev EV_KEY names, ALL of which must be held together (see
    # manual_exit.py's DEFAULT_BUTTONS docstring for why L1+R1+B is the
    # default -- a three-button combo is harder to trigger by accident
    # during normal play than any single button).
    exit_couch_buttons: list[str] = field(default_factory=lambda: ["BTN_TL", "BTN_TR", "BTN_EAST"])
    exit_couch_hold_seconds: float = 10.0


@dataclass
class WizardConfig:
    # Loopback-only by default — see plans/joystick-notify-v2.md, "Wizard
    # network exposure and auth" for why this must not default to a LAN
    # address, and why a non-loopback bind refuses to start without a
    # password already configured.
    bind_address: str = "127.0.0.1"
    port: int = 8642
    # Which systemd --user unit the wizard's "Restart daemon" button
    # restarts. Configurable, not hardcoded, because a dev/test install
    # (e.g. joystick-notify-v2-test.service, run alongside the real one
    # during this rewrite) is a genuinely different unit than production.
    systemd_service_name: str = "joystick-notify.service"


@dataclass
class JoystickNotifyConfig:
    version: int = CONFIG_SCHEMA_VERSION
    # False until the wizard completes at least once. Daemon state is
    # UNCONFIGURED while this is False — the tray reflects that distinctly
    # from "configured but broken."
    configured: bool = False
    # Whether the daemon acts on controller connect/disconnect events at
    # all -- distinct from the daemon/wizard process being up, which is
    # meant to run 24/7 regardless. Toggled from the tray (right-click) or
    # the status page without restarting anything: the daemon re-reads
    # this fresh from config.toml on every device event rather than
    # trusting a value cached at startup, so a toggle from either the tray
    # or the wizard (two separate processes, no shared memory) takes
    # effect on the very next event with no IPC needed -- see daemon.py's
    # _forward_to_state_machine().
    auto_switch_enabled: bool = True
    display: DisplayConfig = field(default_factory=DisplayConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    cec: CecConfig = field(default_factory=CecConfig)
    timing: TimingConfig = field(default_factory=TimingConfig)
    idle: IdleConfig = field(default_factory=IdleConfig)
    on_connect: ActionConfig = field(default_factory=ActionConfig)
    custom_commands: list[CustomCommand] = field(default_factory=list)
    screen_lock: ScreenLockConfig = field(default_factory=ScreenLockConfig)
    cursor: CursorConfig = field(default_factory=CursorConfig)
    shortcuts: ShortcutConfig = field(default_factory=ShortcutConfig)
    wizard: WizardConfig = field(default_factory=WizardConfig)
    shutdown: ShutdownConfig = field(default_factory=ShutdownConfig)
