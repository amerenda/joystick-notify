"""A bounded, cross-process-readable log of pipeline events — for the
wizard's "what just happened" view, requested directly after live
troubleshooting made clear that ad hoc log files with no fixed location
and no UI visibility make diagnosing a live issue slower than it needs to
be.

Captures every level (DEBUG and up) from the "joystick_notify" logger tree
via a `logging.Handler` — reusing the log lines already written throughout
the pipeline rather than instrumenting every call site a second time with
a separate mechanism. Filtering by verbosity happens at *read* time (see
`filter_events()`), not at capture time: the wizard needs to answer "what
was actually happening right before this broke" retroactively, and a
level you didn't think to "turn on" until after the fact would be gone if
capture itself excluded it.

`HEADLINE` is a level between INFO and WARNING for the small set of true
main-lifecycle events (controller connected, mode transitions, teardown
decisions, manual-exit fired, daemon start/stop) — deliberately its own
tier, not just "some INFO calls," so the wizard's default view (Main
events: HEADLINE and up) shows the handful of things that actually matter
at a glance, while routine operational detail (a sink switch, a display
attempt counter, a CEC retry) stays reachable one dropdown step down
under "Info" without crowding out the headlines by default. Existing
WARNING/ERROR calls already sit above HEADLINE numerically, so they
surface in the default view unchanged — only the INFO-level "this is
really a headline" call sites needed reclassifying.
"""
from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from .atomic_json import atomic_write_json
from .health import default_state_dir

MAX_EVENTS = 2000

HEADLINE = 25
logging.addLevelName(HEADLINE, "HEADLINE")

# Ordered thresholds for the wizard's verbosity dropdown -- key is the
# value that comes back in the request, value is the minimum levelno to
# include. "main" is the default, most-reduced view.
LEVEL_THRESHOLDS = {
    "main": HEADLINE,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "debug": logging.DEBUG,
}
DEFAULT_LEVEL_FILTER = "main"


def headline(logger_obj: logging.Logger, msg: str, *args, **kwargs) -> None:
    """Logs at HEADLINE -- see the module docstring for what belongs here.
    Everything else keeps using logger.info/warning/error/debug as usual.
    """
    logger_obj.log(HEADLINE, msg, *args, **kwargs)


@dataclass
class LogEvent:
    timestamp: float
    level: str
    levelno: int
    logger: str
    message: str

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "level": self.level,
            "levelno": self.levelno,
            "logger": self.logger,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LogEvent":
        # levelno is new -- older events.json files written before this
        # field existed fall back to resolving it from the level name so
        # a stale on-disk file doesn't break filtering.
        levelno = d.get("levelno")
        if levelno is None:
            levelno = logging.getLevelName(d["level"])
            if not isinstance(levelno, int):
                levelno = logging.INFO
        return cls(timestamp=d["timestamp"], level=d["level"], levelno=levelno, logger=d["logger"], message=d["message"])


def default_event_log_path() -> Path:
    return default_state_dir() / "events.json"


class EventLogHandler(logging.Handler):
    """Attach to the "joystick_notify" logger to capture every level into a
    bounded ring buffer, persisted to disk so the wizard (a separate
    process) can read it — same cross-process pattern as health.py's
    registry. See the module docstring for why capture is unfiltered and
    filtering happens at read time instead.
    """

    def __init__(self, path: Path | None = None, max_events: int = MAX_EVENTS):
        super().__init__(level=logging.DEBUG)
        self._path = path or default_event_log_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._events: deque[LogEvent] = deque(maxlen=max_events)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:
            message = record.msg
        event = LogEvent(
            timestamp=record.created, level=record.levelname, levelno=record.levelno,
            logger=record.name, message=message,
        )
        self._events.append(event)
        self._persist()

    def _persist(self) -> None:
        payload = {"events": [e.to_dict() for e in self._events]}
        atomic_write_json(self._path, payload, prefix=".events-")


def read_events(path: Path | None = None) -> list[LogEvent]:
    path = path or default_event_log_path()
    try:
        with open(path) as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return [LogEvent.from_dict(d) for d in raw.get("events", [])]


def filter_events(events: list[LogEvent], level_filter: str) -> list[LogEvent]:
    """Applies one of the wizard dropdown's verbosity thresholds. An
    unrecognized filter value falls back to the default rather than
    raising -- a stale bookmarked URL/query-param shouldn't 500 the page.
    """
    threshold = LEVEL_THRESHOLDS.get(level_filter, LEVEL_THRESHOLDS[DEFAULT_LEVEL_FILTER])
    return [e for e in events if e.levelno >= threshold]
