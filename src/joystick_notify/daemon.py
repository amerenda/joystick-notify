"""Entrypoint: asyncio event loop wiring devices -> debounce -> state
machine -> actions (CEC/display/audio/launch), Health registry startup
checks, and `--doctor` — the one-shot self-test explicitly requested by
the v1 audit, calling the *exact same* check_startup_health() the running
daemon calls at boot, so the self-test can never silently drift out of
sync with what actually gets checked at runtime.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import signal
import sys
from pathlib import Path

from .actions import audio as audio_actions
from .actions import cec_control
from .actions import display as display_actions
from .actions import launchers
from .actions import screen_lock as screen_lock_actions
from .activity_gate import ActivityGate
from .config import store as config_store
from .config.schema import JoystickNotifyConfig
from .debounce import Debouncer, DeviceEvent
from .devices import cec as cec_discover
from .devices.detect import HidrawLivenessWatcher, UdevWatcher, device_present
from .event_log import headline
from .health import HEARTBEAT_INTERVAL_SECONDS, Health
from .manual_exit import ManualExitWatcher
from .session_env import ensure_session_environment
from .supervisor import supervise
from .state_machine import ActionHooks, StateMachine

logger = logging.getLogger(__name__)

REQUIRED_BINARIES = ["kscreen-doctor", "pactl"]


def check_startup_health(config: JoystickNotifyConfig, health: Health) -> bool:
    """The pre-flight check requested by the v1 audit: required binaries
    exist, CEC adapter presence (if CEC is enabled), config is loadable.
    Called by both --doctor and daemon startup — see module docstring.
    """
    ok = True
    for binary in REQUIRED_BINARIES:
        if shutil.which(binary) is None:
            health.failed("deps", f"{binary} not found")
            ok = False
    if ok:
        health.ok("deps", "all required binaries present")

    if config.cec.enabled:
        if shutil.which("cec-ctl") is None:
            health.failed("deps", "cec-ctl not found but CEC is enabled in config")
            ok = False
        elif not cec_discover.discover_adapters():
            health.failed("cec", "CEC enabled in config but no /dev/cec* device found")
            ok = False
        else:
            health.ok("cec", "adapter present")
    else:
        # No CEC configured is a complete, valid, first-class end state —
        # not degraded, not failed. See plans/joystick-notify-v2.md's CEC
        # detection strategy section.
        health.ok("cec", "CEC disabled in config")

    if not config.configured:
        health.degraded("wizard", "not yet configured — open the wizard to finish setup")

    return ok


async def _forward_to_state_machine(
    sm: StateMachine, event: DeviceEvent, config_path: Path | None
) -> None:
    """Auto-switch gate between the debounced/trusted device event stream
    and the state machine: re-reads config.auto_switch_enabled fresh from
    disk on every single event, rather than trusting a value cached at
    daemon startup. This is deliberate, not an oversight -- the daemon and
    wizard are separate concerns sharing one process, but the tray (a
    genuinely separate OS process) has no shared memory with either one,
    and toggling "auto-switch" needs to work identically from both the
    tray's right-click menu and the wizard's status page. Both already
    only ever talk to each other through config.toml, so re-reading it
    here (a small, cheap TOML parse) gets a toggle from either source
    applied on the very next controller event, with no IPC/socket needed.

    Disabling auto-switch only ever gates whether a NEW connect/disconnect
    event reaches the state machine -- it deliberately does not touch a
    couch session already in progress (owner-watch teardown, idle
    timeout, the manual-exit shortcut, or the wizard's manual mode-switch
    buttons all keep working exactly as before). "Stop reacting to the
    controller" is the whole and only meaning of the toggle.
    """
    if not config_store.load(config_path).auto_switch_enabled:
        logger.info(
            "daemon: auto-switch disabled, ignoring %s event for %s", event.kind.value, event.device_id
        )
        return
    await sm.handle_device_event(event)


class CouchSessionResources:
    """Owns state that's scoped to a single couch-mode session and needs a
    matching teardown when it ends: the CEC active-source retry task, the
    held screen-lock inhibit cookie, and the manual-exit shortcut watcher.

    Plain attributes on one object, not `nonlocal` closure variables in
    build_hooks() -- confirmed growing three times already (CEC retry
    task, screen-lock cookie, manual-exit watcher), each addition meaning
    another nonlocal declaration to remember in both activate_couch() and
    activate_desk(). A fourth couch-scoped resource is now one attribute,
    not two edited functions.
    """

    def __init__(self, manual_exit_watcher: ManualExitWatcher) -> None:
        self.manual_exit_watcher = manual_exit_watcher
        self.cec_retry_task: asyncio.Task | None = None
        self.screen_lock_cookie: str | None = None


def build_hooks(config: JoystickNotifyConfig, health: Health, manual_exit_watcher: ManualExitWatcher) -> ActionHooks:
    resources = CouchSessionResources(manual_exit_watcher)

    async def _cec_adapter() -> str | None:
        if config.cec.adapter:
            return config.cec.adapter
        return await cec_discover.ensure_adapter(health)

    async def activate_couch(device_id: str) -> None:
        # First, so nothing else that follows is hidden behind a lock
        # screen — display/CEC/audio/launch all still proceed regardless,
        # but the user should actually be able to see the result.
        resources.screen_lock_cookie = await screen_lock_actions.activate_couch(config.screen_lock, health)
        if config.cec.enabled:
            adapter = await _cec_adapter()
            if adapter is None:
                health.failed("cec", "CEC enabled but no adapter found at activation time")
            else:
                resources.cec_retry_task = await cec_control.wake_and_select_input(
                    adapter,
                    config.cec.active_source_phys_addr or None,
                    health,
                    wake_delay_s=config.cec.wake_delay_s,
                    retries=config.cec.active_source_retries,
                    retry_delay_s=config.cec.active_source_retry_delay_s,
                )
                health.ok("cec", "wake + active-source sent")
        await display_actions.activate_couch(config.display, health)
        await audio_actions.activate_couch(config.audio, health)
        if config.shortcuts.exit_couch_enabled:
            await resources.manual_exit_watcher.start(device_id)

    async def activate_desk() -> None:
        await resources.manual_exit_watcher.stop()
        if resources.cec_retry_task is not None:
            resources.cec_retry_task.cancel()
            resources.cec_retry_task = None
        # Exit the launched process BEFORE the display switch, not after:
        # confirmed live 2026-08-22 that leaving Big Picture running
        # across a display-mode change (even just until the *next* couch
        # entry) is what caused a fullscreen-exclusive Steam window to
        # lose HDMI signal entirely -- see
        # launchers.launch_steam_bigpicture()'s docstring for the full
        # root cause. Getting rid of it before the desk resolution switch
        # avoids the same race in the other direction.
        if config.on_connect.run and config.on_connect.kill_on_desk:
            await launchers.exit_launched(config.on_connect.run)
        # Display/audio/screen-lock FIRST, CEC standby LAST: CEC standby is
        # best-effort and can legitimately take a long time (up to
        # standby_verify_attempts * standby_verify_delay_s *PER TARGET* --
        # confirmed live 2026-08-22, ~30s across two targets when the TV
        # simply wasn't responding to standby at all). It used to run
        # first, which meant a slow or failing CEC negotiation held the
        # user's actual monitor/audio switch hostage behind it. The switch
        # that matters every time should never wait on the part that's
        # allowed to fail.
        await display_actions.activate_desk(config.display, health)
        await audio_actions.activate_desk(config.audio, health)
        await screen_lock_actions.activate_desk(config.screen_lock, health, resources.screen_lock_cookie)
        resources.screen_lock_cookie = None
        if config.cec.enabled and config.cec.power_off_on_teardown:
            adapter = await _cec_adapter()
            if adapter is not None:
                await cec_control.standby_and_verify(
                    adapter,
                    config.cec.standby_targets,
                    health,
                    phys_addr=config.cec.active_source_phys_addr or None,
                    attempts=config.cec.standby_verify_attempts,
                    delay_s=config.cec.standby_verify_delay_s,
                )

    async def launch() -> None:
        if config.on_connect.run:
            await launchers.launch(config.on_connect.run)

    async def is_launch_process_alive() -> bool:
        if config.on_connect.run:
            return await launchers.is_launch_process_alive(config.on_connect.run)
        return True

    async def has_launch_target() -> bool:
        return bool(config.on_connect.run)

    async def is_owner_present(device_id: str) -> bool:
        return device_present(device_id)

    async def on_reconnect_while_couch(device_id: str) -> None:
        # Restarts the manual-exit shortcut watcher against whatever evdev
        # node the reconnect landed on -- a brief disconnect/reconnect
        # within disconnect_grace_s never tears couch mode down, but the
        # watcher already exited (OSError on the now-dead node) and nothing
        # else would ever restart it for the rest of this session.
        if config.shortcuts.exit_couch_enabled:
            await resources.manual_exit_watcher.start(device_id)

    async def enter_couch_idle() -> None:
        # Owner absent, game still running: screensaver + TV standby,
        # WITHOUT touching display/audio or leaving Mode.COUCH -- the
        # couch session stays fully set up so a reconnect resumes
        # instantly instead of redoing the whole desk->couch activation.
        await screen_lock_actions.activate_screensaver(health)
        if config.cec.enabled and config.cec.power_off_on_teardown:
            adapter = await _cec_adapter()
            if adapter is not None:
                await cec_control.standby_and_verify(
                    adapter,
                    config.cec.standby_targets,
                    health,
                    phys_addr=config.cec.active_source_phys_addr or None,
                    attempts=config.cec.standby_verify_attempts,
                    delay_s=config.cec.standby_verify_delay_s,
                )

    async def exit_couch_idle() -> None:
        # Wakes the TV back up and dismisses the screensaver -- the mirror
        # of enter_couch_idle(), fired on reconnect (see
        # on_reconnect_while_couch's sibling handling in state_machine.py).
        if config.cec.enabled:
            adapter = await _cec_adapter()
            if adapter is not None:
                # Store the returned retry-loop task the same way
                # activate_couch() does, so a later activate_desk() cancels
                # THIS one too, not just whatever activate_couch() spawned
                # (which has almost certainly already finished its own
                # bounded retries by the time an idle session wakes back up).
                resources.cec_retry_task = await cec_control.wake_and_select_input(
                    adapter,
                    config.cec.active_source_phys_addr or None,
                    health,
                    wake_delay_s=config.cec.wake_delay_s,
                    retries=config.cec.active_source_retries,
                    retry_delay_s=config.cec.active_source_retry_delay_s,
                )
        await screen_lock_actions.deactivate_screensaver(health)

    return ActionHooks(
        activate_couch=activate_couch,
        activate_desk=activate_desk,
        launch=launch,
        is_launch_process_alive=is_launch_process_alive,
        is_owner_present=is_owner_present,
        on_reconnect_while_couch=on_reconnect_while_couch,
        has_launch_target=has_launch_target,
        enter_couch_idle=enter_couch_idle,
        exit_couch_idle=exit_couch_idle,
    )


async def run_daemon(config_path: Path | None = None) -> None:
    config = config_store.load(config_path)
    health = Health()
    check_startup_health(config, health)

    # `sm` doesn't exist yet at the line below, but this callback isn't
    # actually invoked until a shortcut fires later, well after `sm` is
    # assigned — Python closures resolve free variables at call time, not
    # definition time, so this forward reference is safe.
    async def on_manual_exit() -> None:
        await sm.force_exit_to_desk()

    manual_exit_watcher = ManualExitWatcher(
        on_manual_exit,
        health,
        buttons=config.shortcuts.exit_couch_buttons,
        hold_seconds=config.shortcuts.exit_couch_hold_seconds,
    )

    hooks = build_hooks(config, health, manual_exit_watcher)
    sm = StateMachine(
        hooks,
        health,
        disconnect_grace_s=config.timing.disconnect_grace_s,
        launch_startup_grace_s=config.timing.launch_startup_grace_s,
        idle_after_s=config.idle.idle_after_s,
        poll_interval_s=config.timing.poll_interval_s,
        wait_for_game_on_disconnect=config.idle.wait_for_game,
        screensaver_enabled=config.idle.screensaver_enabled,
    )

    # If the configured game is already running right now, that's direct
    # evidence of a restart mid-session (see StateMachine's docstring) --
    # arm a resync watch before any device events start flowing.
    await sm.check_for_stale_session_at_startup()

    async def to_state_machine(event: DeviceEvent) -> None:
        await _forward_to_state_machine(sm, event, config_path)

    # Debounce says a signal is stable; the gate says whether it's
    # trustworthy — a device present at startup (or plugged in only to
    # charge) must prove real activity before it can ever trigger a mode
    # switch. See activity_gate.py's module docstring for the real bug
    # this closes (2026-08-21: a stale Puck receiver connection triggered
    # couch mode on daemon startup with nobody touching it).
    gate = ActivityGate(to_state_machine, health)

    async def emit(event: DeviceEvent) -> None:
        await gate.handle(event)

    debouncer = Debouncer(
        emit,
        health,
        default_debounce_ms=config.timing.debounce_default_ms,
        per_class_debounce_ms=config.timing.debounce_per_class_ms,
    )

    udev_watcher = UdevWatcher(debouncer.feed, health)
    udev_watcher.start()
    liveness_watcher = HidrawLivenessWatcher(debouncer.feed, health)
    liveness_watcher.start()

    # The wizard is served from this same process (one package, one daemon
    # process — see plans/joystick-notify-v2.md, "Proposed file/repo
    # organization"): if the daemon isn't running, there's nothing
    # pretending to be configured, and the tray reflects that directly
    # rather than the wizard silently 404ing from a separate process.
    wizard_server = None
    wizard_task: asyncio.Task | None = None
    try:
        import uvicorn

        from .wizard import auth as wizard_auth
        from .wizard.server import create_app

        creds = wizard_auth.load_credentials()
        wizard_auth.validate_bind_address(config.wizard.bind_address, has_credentials=creds is not None)
        uvicorn_config = uvicorn.Config(
            create_app(sm), host=config.wizard.bind_address, port=config.wizard.port, log_level="warning"
        )
        wizard_server = uvicorn.Server(uvicorn_config)
        wizard_server.install_signal_handlers = lambda: None  # daemon owns SIGTERM/SIGINT, not uvicorn
        wizard_task = supervise("wizard", wizard_server.serve(), health)
        health.ok("wizard", f"listening on {config.wizard.bind_address}:{config.wizard.port}")
    except ValueError as e:
        # Refused bind (LAN address with no password configured) — loud,
        # not a silent fallback to loopback.
        health.failed("wizard", "refused to start", str(e))
        logger.error("daemon: wizard server refused to start: %s", e)
    except Exception as e:
        health.failed("wizard", "failed to start embedded wizard server", str(e))
        logger.exception("daemon: wizard server failed to start")

    stop_event = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass  # signal handlers unsupported on this platform (e.g. some test runners)

    headline(logger, "daemon: started, mode=%s configured=%s", sm.mode.value, config.configured)
    try:
        while not stop_event.is_set():
            health.heartbeat()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=HEARTBEAT_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass
    finally:
        headline(logger, "daemon: shutting down")
        udev_watcher.stop()
        await liveness_watcher.stop()
        await debouncer.aclose()
        await gate.aclose()
        await sm.aclose()
        await manual_exit_watcher.stop()
        if wizard_server is not None:
            wizard_server.should_exit = True
        if wizard_task is not None:
            try:
                await asyncio.wait_for(wizard_task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                wizard_task.cancel()


def run_doctor(config: JoystickNotifyConfig, health: Health) -> int:
    ok = check_startup_health(config, health)
    for name, status in sorted(health.all().items()):
        marker = {"ok": "OK", "degraded": "WARN", "failed": "FAIL"}[status.status.value]
        print(f"[{marker:4}] {name}: {status.reason}" + (f" ({status.detail})" if status.detail else ""))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ensure_session_environment()

    parser = argparse.ArgumentParser(prog="jn-daemon")
    parser.add_argument("--doctor", action="store_true", help="one-shot self-test; exit non-zero on failure")
    parser.add_argument("--config", type=str, default=None)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    log_format = "%(asctime)s %(name)s %(levelname)s %(message)s"
    logging.basicConfig(level=args.log_level, format=log_format)

    # Always write to a fixed, discoverable location regardless of how the
    # process is launched (systemd unit, manual shell, nohup'd background
    # process) -- requested directly after live troubleshooting made clear
    # that ad hoc log file locations chosen per-launch aren't discoverable.
    from .health import default_state_dir

    log_dir = default_state_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    # RotatingFileHandler, not a plain FileHandler: this is a long-running
    # systemd user service with no external log rotation configured for it
    # (unlike journald, which the ExecStart's stdout/stderr already flow
    # through) -- a plain FileHandler grows daemon.log forever.
    from logging.handlers import RotatingFileHandler

    file_handler = RotatingFileHandler(
        log_dir / "daemon.log", maxBytes=10 * 1024 * 1024, backupCount=3
    )
    file_handler.setFormatter(logging.Formatter(log_format))
    logging.getLogger().addHandler(file_handler)

    # Bounded, cross-process-readable event log (INFO+ only) for the
    # wizard's "what just happened" view -- see event_log.py.
    from .event_log import EventLogHandler

    logging.getLogger("joystick_notify").addHandler(EventLogHandler())

    config_path = Path(args.config) if args.config else None
    config = config_store.load(config_path)

    if args.doctor:
        return run_doctor(config, Health())

    try:
        asyncio.run(run_daemon(config_path))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
