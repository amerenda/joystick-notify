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
    allm_enabled: bool = True
    # Logical addresses (0 = TV, 5 = Audio System/receiver) to standby+verify
    # on teardown. Auto-populated by the wizard's topology scan, not
    # hand-typed (generalizes v1's hardcoded CEC_STANDBY_TARGETS).
    standby_targets: list[int] = field(default_factory=lambda: [0])
    standby_verify_attempts: int = 3
    standby_verify_delay_s: float = 2.0
    selfheal_cooldown_s: float = 120.0


@dataclass
class TimingConfig:
    disconnect_grace_s: float = 30.0
    launch_startup_grace_s: float = 10.0
    no_controller_timeout_s: float = 120.0
    poll_interval_s: float = 2.0
    debounce_default_ms: int = 300
    # Per-device-class overrides — e.g. a Steam Puck receiver and an 8BitDo
    # dongle don't necessarily bounce identically. Keyed by profile id from
    # devices/profiles.py.
    debounce_per_class_ms: dict[str, int] = field(default_factory=dict)


@dataclass
class ActionConfig:
    # Resolved against detected CEC targets, e.g. ["cec:tv", "cec:receiver"].
    power_on: list[str] = field(default_factory=list)
    # Launcher preset id (from actions/launchers.py) or a custom shell command.
    run: str = ""


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
class WizardConfig:
    # Loopback-only by default — see plans/joystick-notify-v2.md, "Wizard
    # network exposure and auth" for why this must not default to a LAN
    # address, and why a non-loopback bind refuses to start without a
    # password already configured.
    bind_address: str = "127.0.0.1"
    port: int = 8642


@dataclass
class JoystickNotifyConfig:
    version: int = CONFIG_SCHEMA_VERSION
    # False until the wizard completes at least once. Daemon state is
    # UNCONFIGURED while this is False — the tray reflects that distinctly
    # from "configured but broken."
    configured: bool = False
    display: DisplayConfig = field(default_factory=DisplayConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    cec: CecConfig = field(default_factory=CecConfig)
    timing: TimingConfig = field(default_factory=TimingConfig)
    on_connect: ActionConfig = field(default_factory=ActionConfig)
    on_disconnect: ActionConfig = field(default_factory=ActionConfig)
    screen_lock: ScreenLockConfig = field(default_factory=ScreenLockConfig)
    wizard: WizardConfig = field(default_factory=WizardConfig)
