"""A bounded, cross-process-readable log of high-level pipeline events —
for the wizard's "what just happened" view, requested directly after live
troubleshooting made clear that ad hoc log files with no fixed location
and no UI visibility make diagnosing a live issue slower than it needs to
be.

Deliberately reuses the INFO-level log lines already written throughout
the pipeline (debounce settlements, activity-gate decisions, state
transitions, action outcomes) via a `logging.Handler`, rather than
instrumenting every call site a second time with a second logging
mechanism. DEBUG-level raw-event spam (every bounce, every udev event) is
excluded by construction — only the same "big step" lines a human
tailing the log would care about.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from .health import default_state_dir

MAX_EVENTS = 200


@dataclass
class LogEvent:
    timestamp: float
    level: str
    logger: str
    message: str

    def to_dict(self) -> dict:
        return {"timestamp": self.timestamp, "level": self.level, "logger": self.logger, "message": self.message}

    @classmethod
    def from_dict(cls, d: dict) -> "LogEvent":
        return cls(timestamp=d["timestamp"], level=d["level"], logger=d["logger"], message=d["message"])


def default_event_log_path() -> Path:
    return default_state_dir() / "events.json"


class EventLogHandler(logging.Handler):
    """Attach to the "joystick_notify" logger to capture INFO+ records
    into a bounded ring buffer, persisted to disk so the wizard (a
    separate process) can read it — same cross-process pattern as
    health.py's registry.
    """

    def __init__(self, path: Path | None = None, max_events: int = MAX_EVENTS):
        super().__init__(level=logging.INFO)
        self._path = path or default_event_log_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._events: deque[LogEvent] = deque(maxlen=max_events)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:
            message = record.msg
        event = LogEvent(timestamp=record.created, level=record.levelname, logger=record.name, message=message)
        self._events.append(event)
        self._persist()

    def _persist(self) -> None:
        payload = {"events": [e.to_dict() for e in self._events]}
        fd, tmp_path = tempfile.mkstemp(dir=self._path.parent, prefix=".events-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f)
            os.replace(tmp_path, self._path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


def read_events(path: Path | None = None) -> list[LogEvent]:
    path = path or default_event_log_path()
    try:
        with open(path) as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return [LogEvent.from_dict(d) for d in raw.get("events", [])]
