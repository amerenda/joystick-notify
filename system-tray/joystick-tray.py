#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


SERVICE = "joystick-notify.service"


def systemctl_user(*args: str) -> subprocess.CompletedProcess:
    return run(["systemctl", "--user", *args, SERVICE])


def is_active() -> bool:
    cp = systemctl_user("is-active")
    return cp.returncode == 0 and cp.stdout.strip() == "active"


def pick_terminal() -> list[str] | None:
    # Prefer Konsole on Plasma.
    if shutil.which("konsole"):
        return ["konsole", "-e"]
    if shutil.which("xterm"):
        return ["xterm", "-e"]
    return None


def open_logs():
    term = pick_terminal()
    cmd = [
        "bash",
        "-lc",
        f"journalctl --user -u {SERVICE} -n 200 --no-pager; echo; read -n 1 -rsp 'press any key to close'",
    ]
    if term:
        subprocess.Popen([*term, *cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        # No terminal available; best-effort print to stderr.
        cp = run(cmd)
        sys.stderr.write(cp.stdout)
        sys.stderr.write(cp.stderr)


def main() -> int:
    try:
        from PyQt6.QtCore import Qt, QTimer
        from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
        from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
    except Exception as e:
        sys.stderr.write(
            "PyQt6 is required for joystick-notify tray.\n"
            "Install on Arch: pacman -S --needed python-pyqt6\n"
            f"Import error: {e}\n"
        )
        return 1

    # Ensure consistent app identity for the tray.
    app = QApplication(sys.argv)
    app.setApplicationName("joystick-notify-tray")
    # Helps xdg-desktop-portal / status notifier register the application.
    # Requires a matching .desktop file (we install joystick-notify-tray.desktop).
    app.setDesktopFileName("joystick-notify-tray")
    app.setQuitOnLastWindowClosed(False)

    icon_active = QIcon.fromTheme("input-gaming")
    icon_paused = QIcon.fromTheme("media-playback-pause")
    if icon_active.isNull():
        icon_active = QIcon.fromTheme("applications-games")
    if icon_paused.isNull():
        icon_paused = QIcon.fromTheme("process-stop")

    def dot_icon(rgb: tuple[int, int, int]) -> QIcon:
        pm = QPixmap(64, 64)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(*rgb))
        p.drawEllipse(8, 8, 48, 48)
        p.end()
        return QIcon(pm)

    if icon_active.isNull():
        icon_active = dot_icon((0, 190, 0))
    if icon_paused.isNull():
        icon_paused = dot_icon((180, 180, 180))

    tray = QSystemTrayIcon(icon_paused)
    tray.setVisible(True)

    menu = QMenu()

    status_action = QAction("Status: …")
    status_action.setEnabled(False)
    menu.addAction(status_action)
    menu.addSeparator()

    start_action = QAction("Start joystick-notify")
    stop_action = QAction("Stop joystick-notify")
    restart_action = QAction("Restart joystick-notify")
    logs_action = QAction("View logs")
    quit_action = QAction("Quit tray")

    menu.addAction(start_action)
    menu.addAction(stop_action)
    menu.addAction(restart_action)
    menu.addSeparator()
    menu.addAction(logs_action)
    menu.addSeparator()
    menu.addAction(quit_action)

    tray.setContextMenu(menu)

    def refresh():
        active = is_active()
        if active:
            tray.setIcon(icon_active)
            tray.setToolTip("joystick-notify: Active")
            status_action.setText("Status: Active")
            start_action.setEnabled(False)
            stop_action.setEnabled(True)
            restart_action.setEnabled(True)
        else:
            tray.setIcon(icon_paused)
            tray.setToolTip("joystick-notify: Paused")
            status_action.setText("Status: Paused")
            start_action.setEnabled(True)
            stop_action.setEnabled(False)
            restart_action.setEnabled(False)

    def start():
        systemctl_user("start")
        refresh()

    def stop():
        systemctl_user("stop")
        refresh()

    def restart():
        systemctl_user("restart")
        refresh()

    start_action.triggered.connect(start)
    stop_action.triggered.connect(stop)
    restart_action.triggered.connect(restart)
    logs_action.triggered.connect(open_logs)
    quit_action.triggered.connect(app.quit)

    refresh()

    timer = QTimer()
    timer.setInterval(2000)
    timer.timeout.connect(refresh)
    timer.start()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())



