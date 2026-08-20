"""pactl-based sink resolution/routing — ports lib/audio-control.sh.
Parsing functions are pure and unit-tested against canned `pactl` output
(see tests/test_audio.py); subprocess orchestration is real I/O.

Unlike display switching, an unresolved audio sink is genuinely
best-effort here (matches v1: couch_mode_active logs a warning and
continues rather than falling back to desk mode) — sound routing to the
wrong device is annoying, not "the feature doesn't work."
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

from ..config.schema import AudioConfig
from ..health import Health

logger = logging.getLogger(__name__)

PACTL_TIMEOUT_S = 5.0
_SINK_HEADER_RE = re.compile(r"^Sink #\d+")
_ALSA_CARD_RE = re.compile(r'alsa\.card\s*=\s*"?([^"\s]+)"?')
_ALSA_DEVICE_RE = re.compile(r'alsa\.device\s*=\s*"?([^"\s]+)"?')


@dataclass
class SinkInfo:
    name: str = ""
    description: str = ""
    alsa_card: str = ""
    alsa_device: str = ""


def parse_sinks(pactl_list_sinks_output: str) -> list[SinkInfo]:
    sinks: list[SinkInfo] = []
    current: SinkInfo | None = None
    for raw_line in pactl_list_sinks_output.splitlines():
        line = raw_line.strip()
        if _SINK_HEADER_RE.match(line):
            if current is not None:
                sinks.append(current)
            current = SinkInfo()
            continue
        if current is None:
            continue
        if line.startswith("Name:"):
            current.name = line.split(":", 1)[1].strip()
        elif line.startswith("Description:"):
            current.description = line.split(":", 1)[1].strip()
        elif line.startswith("alsa.card"):
            m = _ALSA_CARD_RE.search(line)
            if m:
                current.alsa_card = m.group(1)
        elif line.startswith("alsa.device"):
            m = _ALSA_DEVICE_RE.search(line)
            if m:
                current.alsa_device = m.group(1)
    if current is not None:
        sinks.append(current)
    return sinks


def resolve_sink_by_alsa(sinks: list[SinkInfo], want_card: str, want_dev: str) -> str | None:
    for s in sinks:
        if s.alsa_card == want_card and s.alsa_device == want_dev:
            return s.name
    return None


def resolve_sink_by_description(sinks: list[SinkInfo], want_desc: str) -> str | None:
    for s in sinks:
        if s.description == want_desc:
            return s.name
    return None


def resolve_hdmi_sink(short_sinks_output: str) -> str | None:
    for line in short_sinks_output.splitlines():
        parts = line.split()
        if len(parts) >= 2 and "hdmi" in parts[1].lower():
            return parts[1]
    return None


async def _pactl(*args: str, timeout: float = PACTL_TIMEOUT_S) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "pactl", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
    except FileNotFoundError:
        return -1, "pactl not found"
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, "timeout"
    return proc.returncode, out.decode(errors="replace")


async def list_sinks() -> list[SinkInfo]:
    rc, out = await _pactl("list", "sinks")
    return parse_sinks(out) if rc == 0 else []


async def resolve_couch_sink(config: AudioConfig) -> str | None:
    if config.couch_sink:
        return config.couch_sink
    rc, short_out = await _pactl("list", "short", "sinks")
    if rc == 0:
        hdmi = resolve_hdmi_sink(short_out)
        if hdmi:
            return hdmi
    sinks = await list_sinks()
    return resolve_sink_by_description(sinks, "Couch")


async def resolve_couch_sink_with_wait(config: AudioConfig, *, attempts: int = 40, delay_s: float = 0.25) -> str | None:
    for _ in range(attempts):
        sink = await resolve_couch_sink(config)
        if sink:
            return sink
        await asyncio.sleep(delay_s)
    return None


async def set_default_sink(sink: str) -> None:
    await _pactl("set-default-sink", sink)
    logger.info("audio: default -> %s", sink)


async def move_all_sink_inputs_to(sink: str) -> None:
    rc, out = await _pactl("list", "short", "sink-inputs")
    if rc != 0:
        return
    ids = [line.split()[0] for line in out.splitlines() if line.strip()]
    moved = 0
    for id_ in ids:
        rc, _ = await _pactl("move-sink-input", id_, sink)
        if rc == 0:
            moved += 1
    logger.info("audio: moved %d sink-input(s) -> %s", moved, sink)


async def set_audio_to_sink(sink: str) -> None:
    await set_default_sink(sink)
    await move_all_sink_inputs_to(sink)


async def activate_desk(config: AudioConfig, health: Health) -> None:
    if not config.desk_sink:
        health.degraded("audio", "no desk sink configured")
        return
    await set_audio_to_sink(config.desk_sink)
    health.ok("audio", f"desk sink {config.desk_sink} active")


async def activate_couch(config: AudioConfig, health: Health) -> None:
    sink = await resolve_couch_sink_with_wait(config)
    if not sink:
        health.degraded("audio", "could not resolve couch sink after waiting")
        return
    await set_audio_to_sink(sink)
    health.ok("audio", f"couch sink {sink} active")
