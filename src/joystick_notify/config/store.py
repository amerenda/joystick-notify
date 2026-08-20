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
    DisplayConfig,
    JoystickNotifyConfig,
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


def _from_dict(raw: dict) -> JoystickNotifyConfig:
    defaults = JoystickNotifyConfig()
    return JoystickNotifyConfig(
        version=raw.get("version", defaults.version),
        configured=raw.get("configured", defaults.configured),
        display=DisplayConfig(**{**asdict(defaults.display), **raw.get("display", {})}),
        audio=AudioConfig(**{**asdict(defaults.audio), **raw.get("audio", {})}),
        cec=CecConfig(**{**asdict(defaults.cec), **raw.get("cec", {})}),
        timing=TimingConfig(**{**asdict(defaults.timing), **raw.get("timing", {})}),
        on_connect=ActionConfig(**{**asdict(defaults.on_connect), **raw.get("on_connect", {})}),
        on_disconnect=ActionConfig(**{**asdict(defaults.on_disconnect), **raw.get("on_disconnect", {})}),
        wizard=WizardConfig(**{**asdict(defaults.wizard), **raw.get("wizard", {})}),
    )
