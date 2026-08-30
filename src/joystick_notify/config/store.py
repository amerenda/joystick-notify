"""Load/save ~/.config/joystick-notify/config.toml — single source of
truth, read and written by both the daemon and the wizard through this
module only (no parallel env-var pile like v1's config-env.sh).
"""
from __future__ import annotations

import os
import tempfile
import tomllib
from dataclasses import asdict
from pathlib import Path

import tomli_w

from .schema import (
    ActionConfig,
    AudioConfig,
    CecConfig,
    CursorConfig,
    CustomCommand,
    DisplayConfig,
    IdleConfig,
    JoystickNotifyConfig,
    ScreenLockConfig,
    ShortcutConfig,
    TimingConfig,
    WizardConfig,
)


def default_config_dir() -> Path:
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config) if xdg_config else Path.home() / ".config"
    return base / "joystick-notify"


def default_config_path() -> Path:
    return default_config_dir() / "config.toml"


def load(path: Path | None = None) -> JoystickNotifyConfig:
    path = path or default_config_path()
    if not path.exists():
        return JoystickNotifyConfig()
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    return _from_dict(raw)


def save(config: JoystickNotifyConfig, path: Path | None = None) -> None:
    path = path or default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = asdict(config)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            tomli_w.dump(raw, f)
        os.replace(tmp_path, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _section(cls, current, raw_section: dict):
    """Merges a loaded TOML section over a dataclass's current values,
    silently dropping any key that isn't a field on `cls` anymore.

    Without this, removing a config field (as happened to
    cec.allm_enabled/selfheal_cooldown_s and on_connect.power_on/
    on_disconnect) would make _from_dict() raise TypeError on anyone's
    existing config.toml that still has the old key on disk, rather than
    just ignoring it — a field rename/removal shouldn't be able to break
    loading an old config.
    """
    known = {f for f in asdict(current)}
    filtered = {k: v for k, v in raw_section.items() if k in known}
    return cls(**{**asdict(current), **filtered})


def _custom_commands(raw_list) -> list[CustomCommand]:
    known = {f for f in asdict(CustomCommand())}
    result = []
    for item in raw_list or []:
        if not isinstance(item, dict):
            continue
        filtered = {k: v for k, v in item.items() if k in known}
        result.append(CustomCommand(**filtered))
    return result


def _from_dict(raw: dict) -> JoystickNotifyConfig:
    defaults = JoystickNotifyConfig()
    return JoystickNotifyConfig(
        version=raw.get("version", defaults.version),
        configured=raw.get("configured", defaults.configured),
        auto_switch_enabled=raw.get("auto_switch_enabled", defaults.auto_switch_enabled),
        display=_section(DisplayConfig, defaults.display, raw.get("display", {})),
        audio=_section(AudioConfig, defaults.audio, raw.get("audio", {})),
        cec=_section(CecConfig, defaults.cec, raw.get("cec", {})),
        timing=_section(TimingConfig, defaults.timing, raw.get("timing", {})),
        idle=_section(IdleConfig, defaults.idle, raw.get("idle", {})),
        on_connect=_section(ActionConfig, defaults.on_connect, raw.get("on_connect", {})),
        custom_commands=_custom_commands(raw.get("custom_commands", [])),
        screen_lock=_section(ScreenLockConfig, defaults.screen_lock, raw.get("screen_lock", {})),
        cursor=_section(CursorConfig, defaults.cursor, raw.get("cursor", {})),
        shortcuts=_section(ShortcutConfig, defaults.shortcuts, raw.get("shortcuts", {})),
        wizard=_section(WizardConfig, defaults.wizard, raw.get("wizard", {})),
    )
