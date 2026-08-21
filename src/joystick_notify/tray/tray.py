"""PyQt6 tray icon — polls the Health registry (health.py) instead of
scattered flag files (v1's CEC_BROKEN_FLAG being the one example; every
other subsystem had no tray-visible signal at all). See
plans/joystick-notify-v2.md, "Tray icon states — what counts as broken."

`tray_state()` is the pure decision function (snapshot + config ->
one of five states) and is unit-tested without importing PyQt6 at all
(see tests/test_tray_state.py) — PyQt6 itself is only imported inside
`main()`, same lazy-import pattern v1 used, so this module stays
importable (and testable) on a box with no Qt installed, e.g. CI.
"""
from __future__ import annotations

import subprocess
import sys
import webbrowser
from enum import Enum

from ..config import store as config_store
from ..health import HealthSnapshot, Status, read_snapshot

SERVICE = "joystick-notify.service"
POLL_INTERVAL_MS = 2000


class TrayState(str, Enum):
    UNCONFIGURED = "unconfigured"
    OK = "ok"
    DEGRADED = "degraded"
    BROKEN = "broken"
    DAEMON_UNREACHABLE = "daemon_unreachable"


def tray_state(snapshot: HealthSnapshot | None, configured: bool) -> TrayState:
    """The exact mapping from plans/joystick-notify-v2.md's tray table:
    - No snapshot at all, or a stale heartbeat -> DAEMON_UNREACHABLE. Never
      silently shown as whatever the last real status happened to be.
    - Not yet configured -> UNCONFIGURED takes priority over health detail
      (there's nothing meaningful to report yet).
    - Otherwise: any FAILED component -> BROKEN, any DEGRADED -> DEGRADED,
      else OK (idle-with-nothing-connected is OK, not broken — see
      devices/detect.py's is_candidate_hid / Health docstring).
    """
    if snapshot is None or not snapshot.daemon_alive:
        return TrayState.DAEMON_UNREACHABLE
    if not configured:
        return TrayState.UNCONFIGURED
    overall = snapshot.overall
    if overall == Status.FAILED:
        return TrayState.BROKEN
    if overall == Status.DEGRADED:
        return TrayState.DEGRADED
    return TrayState.OK


def _systemctl_user(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args, SERVICE], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _wizard_url() -> str:
    config = config_store.load()
    host = config.wizard.bind_address
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    return f"http://{host}:{config.wizard.port}/"


def main() -> int:
    from ..session_env import ensure_session_environment

    ensure_session_environment()

    try:
        from PyQt6.QtCore import Qt, QTimer
        from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
        from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
    except ImportError as e:
        sys.stderr.write(
            "PyQt6 is required for the joystick-notify tray.\n"
            "Install: pip install 'joystick-notify[tray]' (or python-pyqt6 on Arch)\n"
            f"Import error: {e}\n"
        )
        return 1

    app = QApplication(sys.argv)
    app.setApplicationName("joystick-notify-tray")
    app.setDesktopFileName("joystick-notify-tray")
    app.setQuitOnLastWindowClosed(False)

    COLORS = {
        TrayState.OK: (62, 207, 108),
        TrayState.DEGRADED: (224, 177, 60),
        TrayState.BROKEN: (224, 92, 92),
        TrayState.UNCONFIGURED: (150, 150, 150),
        TrayState.DAEMON_UNREACHABLE: (70, 70, 70),
    }

    def gamepad_path() -> QPainterPath:
        # Simple recognizable gamepad silhouette: a pill-shaped body with a
        # rounded grip bump on each side, built by unioning basic shapes
        # rather than a hand-authored SVG -- keeps the tray dependency-free
        # (no icon theme, no external asset) while reading clearly at
        # small tray sizes.
        path = QPainterPath()
        path.addRoundedRect(10.0, 22.0, 44.0, 20.0, 10.0, 10.0)  # body
        path.addEllipse(4.0, 26.0, 20.0, 20.0)  # left grip
        path.addEllipse(40.0, 26.0, 20.0, 20.0)  # right grip
        return path.simplified()

    def dot_icon(state: TrayState) -> QIcon:
        pm = QPixmap(64, 64)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(*COLORS[state]))
        p.drawPath(gamepad_path())

        # Thumbstick/D-pad detail: two small darker dots on the body, just
        # enough to read as "controller" rather than an abstract blob.
        detail = QColor(*COLORS[state]).darker(140)
        p.setBrush(detail)
        p.drawEllipse(20, 28, 8, 8)
        p.drawEllipse(36, 28, 8, 8)

        if state == TrayState.BROKEN:
            pen = QPen(QColor(255, 255, 255))
            pen.setWidth(4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(24, 24, 40, 40)
            p.drawLine(40, 24, 24, 40)
        elif state == TrayState.UNCONFIGURED:
            pen = QPen(QColor(255, 255, 255))
            pen.setWidth(3)
            p.setPen(pen)
            p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "?")
        p.end()
        return QIcon(pm)

    ICONS = {state: dot_icon(state) for state in TrayState}

    tray = QSystemTrayIcon(ICONS[TrayState.DAEMON_UNREACHABLE])
    tray.setVisible(True)

    menu = QMenu()
    status_action = QAction("Status: …")
    status_action.setEnabled(False)
    menu.addAction(status_action)
    menu.addSeparator()

    open_wizard_action = QAction("Open wizard")
    start_action = QAction("Start joystick-notify")
    stop_action = QAction("Stop joystick-notify")
    restart_action = QAction("Restart joystick-notify")
    quit_action = QAction("Quit tray")

    menu.addAction(open_wizard_action)
    menu.addSeparator()
    menu.addAction(start_action)
    menu.addAction(stop_action)
    menu.addAction(restart_action)
    menu.addSeparator()
    menu.addAction(quit_action)
    tray.setContextMenu(menu)

    LABELS = {
        TrayState.OK: "Running",
        TrayState.DEGRADED: "Running (degraded)",
        TrayState.BROKEN: "Running (broken)",
        TrayState.UNCONFIGURED: "Not configured",
        TrayState.DAEMON_UNREACHABLE: "Daemon not running",
    }

    def refresh() -> None:
        snapshot = read_snapshot()
        config = config_store.load()
        state = tray_state(snapshot, config.configured)

        tray.setIcon(ICONS[state])
        label = LABELS[state]
        tip = f"joystick-notify: {label}"
        if snapshot is not None and snapshot.daemon_alive:
            for name, c in snapshot.components.items():
                if c.status != Status.OK:
                    tip += f"\n{name}: {c.reason}"
        tray.setToolTip(tip)
        status_action.setText(f"Status: {label}")

    def open_wizard() -> None:
        webbrowser.open(_wizard_url())

    open_wizard_action.triggered.connect(open_wizard)
    start_action.triggered.connect(lambda: (_systemctl_user("start"), refresh()))
    stop_action.triggered.connect(lambda: (_systemctl_user("stop"), refresh()))
    restart_action.triggered.connect(lambda: (_systemctl_user("restart"), refresh()))
    quit_action.triggered.connect(app.quit)

    refresh()
    timer = QTimer()
    timer.setInterval(POLL_INTERVAL_MS)
    timer.timeout.connect(refresh)
    timer.start()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
