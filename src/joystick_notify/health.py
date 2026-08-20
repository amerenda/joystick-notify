"""Structured health reporting: the single interface every subsystem reports
through, and the on-disk registry the tray/wizard read from a separate
process. See plans/joystick-notify-v2.md, "Structured upstream status
reporting" and "Tray icon states" sections for the design this implements.

Deliberately NOT a place to hide failures: `failed()` is for anything that
would stop the daemon from doing its job (see the table in the plan for the
concrete list — missing binaries, missing udev rules, CEC configured but
unreachable, couch display gone). `degraded()` is for "still working, but
worth a human's attention" (e.g. a CEC target found but not yet confirmed).
`ok()` covers the normal idle state too — "no controller plugged in right
now" is OK, not failed; only a broken *detection path* is failed. Callers
must not conflate the two (see devices/detect.py for the concrete case).
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

SCHEMA_VERSION = 1

# How often the daemon touches the heartbeat regardless of whether any
# component's status actually changed. The tray treats a snapshot whose
# heartbeat is older than HEARTBEAT_STALE_SECONDS as "daemon not running" —
# never silently displayed as whatever the last real status happened to be.
HEARTBEAT_INTERVAL_SECONDS = 5
HEARTBEAT_STALE_SECONDS = 15


class Status(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"


@dataclass
class ComponentStatus:
    status: Status
    reason: str = ""
    detail: str = ""
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ComponentStatus":
        return cls(
            status=Status(d["status"]),
            reason=d.get("reason", ""),
            detail=d.get("detail", ""),
            updated_at=d.get("updated_at", 0.0),
        )


def default_state_dir() -> Path:
    xdg_state = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg_state) if xdg_state else Path.home() / ".local" / "state"
    return base / "joystick-notify"


class Health:
    """One interface every subsystem (devices, cec, display, audio,
    launchers, wizard, deps) reports through. All of it lands in one
    in-memory registry, persisted to disk so the tray and wizard — separate
    processes — can read it without a private flag-file-per-condition
    convention (the exact pattern this rewrite retires from v1).
    """

    def __init__(self, path: Path | None = None):
        self._path = path or (default_state_dir() / "health.json")
        self._components: dict[str, ComponentStatus] = {}
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def ok(self, component: str, reason: str = "") -> None:
        self._set(component, Status.OK, reason)

    def degraded(self, component: str, reason: str, detail: str = "") -> None:
        self._set(component, Status.DEGRADED, reason, detail)

    def failed(self, component: str, reason: str, detail: str = "") -> None:
        self._set(component, Status.FAILED, reason, detail)

    def get(self, component: str) -> ComponentStatus | None:
        return self._components.get(component)

    def all(self) -> dict[str, ComponentStatus]:
        return dict(self._components)

    def overall(self) -> Status:
        statuses = {c.status for c in self._components.values()}
        if Status.FAILED in statuses:
            return Status.FAILED
        if Status.DEGRADED in statuses:
            return Status.DEGRADED
        return Status.OK

    def _set(self, component: str, status: Status, reason: str, detail: str = "") -> None:
        self._components[component] = ComponentStatus(status=status, reason=reason, detail=detail)
        self._persist()

    def heartbeat(self) -> None:
        """Touch the heartbeat without changing any component's status —
        called on a timer by the daemon so the tray can distinguish 'daemon
        alive, everything OK' from 'daemon crashed, last snapshot happened
        to say OK'.
        """
        self._persist()

    def _persist(self) -> None:
        snapshot = {
            "schema_version": SCHEMA_VERSION,
            "heartbeat": time.time(),
            "components": {name: c.to_dict() for name, c in self._components.items()},
        }
        fd, tmp_path = tempfile.mkstemp(dir=self._path.parent, prefix=".health-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(snapshot, f)
            os.replace(tmp_path, self._path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


@dataclass
class HealthSnapshot:
    heartbeat: float
    components: dict[str, ComponentStatus]

    @property
    def daemon_alive(self) -> bool:
        return (time.time() - self.heartbeat) < HEARTBEAT_STALE_SECONDS

    @property
    def overall(self) -> Status:
        if not self.daemon_alive:
            raise RuntimeError("overall() is meaningless when daemon_alive is False; check daemon_alive first")
        statuses = {c.status for c in self.components.values()}
        if Status.FAILED in statuses:
            return Status.FAILED
        if Status.DEGRADED in statuses:
            return Status.DEGRADED
        return Status.OK


def read_snapshot(path: Path | None = None) -> HealthSnapshot | None:
    """Read-only view for a separate process (tray, wizard). Returns None if
    no daemon has ever written a snapshot (e.g. first install, before first
    start) — distinct from a stale/crashed daemon, which returns a snapshot
    with daemon_alive=False.
    """
    path = path or (default_state_dir() / "health.json")
    try:
        with open(path) as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    components = {name: ComponentStatus.from_dict(d) for name, d in raw.get("components", {}).items()}
    return HealthSnapshot(heartbeat=raw.get("heartbeat", 0.0), components=components)
