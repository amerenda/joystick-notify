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
from .config import store as config_store
from .config.schema import JoystickNotifyConfig
from .debounce import Debouncer, DeviceEvent
from .devices import cec as cec_discover
from .devices.detect import HidrawLivenessWatcher, UdevWatcher, device_present
from .health import HEARTBEAT_INTERVAL_SECONDS, Health
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


def build_hooks(config: JoystickNotifyConfig, health: Health) -> ActionHooks:
    cec_retry_task: asyncio.Task | None = None

    async def _cec_adapter() -> str | None:
        if config.cec.adapter:
            return config.cec.adapter
        return await cec_discover.ensure_adapter(health)

    async def activate_couch() -> None:
        nonlocal cec_retry_task
        if config.cec.enabled:
            adapter = await _cec_adapter()
            if adapter is None:
                health.failed("cec", "CEC enabled but no adapter found at activation time")
            else:
                cec_retry_task = await cec_control.wake_and_select_input(
                    adapter,
                    config.cec.active_source_phys_addr or None,
                    wake_delay_s=config.cec.wake_delay_s,
                    retries=config.cec.active_source_retries,
                    retry_delay_s=config.cec.active_source_retry_delay_s,
                )
                health.ok("cec", "wake + active-source sent")
        await display_actions.activate_couch(config.display, health)
        await audio_actions.activate_couch(config.audio, health)

    async def activate_desk() -> None:
        nonlocal cec_retry_task
        if cec_retry_task is not None:
            cec_retry_task.cancel()
            cec_retry_task = None
        if config.cec.enabled and config.cec.power_off_on_teardown:
            adapter = await _cec_adapter()
            if adapter is not None:
                await cec_control.standby_and_verify(
                    adapter,
                    config.cec.standby_targets,
                    health,
                    attempts=config.cec.standby_verify_attempts,
                    delay_s=config.cec.standby_verify_delay_s,
                )
        await display_actions.activate_desk(config.display, health)
        await audio_actions.activate_desk(config.audio, health)

    async def launch() -> None:
        if config.on_connect.run:
            await launchers.launch(config.on_connect.run)

    async def is_launch_process_alive() -> bool:
        if config.on_connect.run:
            return await launchers.is_launch_process_alive(config.on_connect.run)
        return True

    async def is_owner_present(device_id: str) -> bool:
        return device_present(device_id)

    return ActionHooks(
        activate_couch=activate_couch,
        activate_desk=activate_desk,
        launch=launch,
        is_launch_process_alive=is_launch_process_alive,
        is_owner_present=is_owner_present,
    )


async def run_daemon(config_path: Path | None = None) -> None:
    config = config_store.load(config_path)
    health = Health()
    check_startup_health(config, health)

    hooks = build_hooks(config, health)
    sm = StateMachine(
        hooks,
        health,
        disconnect_grace_s=config.timing.disconnect_grace_s,
        launch_startup_grace_s=config.timing.launch_startup_grace_s,
        no_controller_timeout_s=config.timing.no_controller_timeout_s,
        poll_interval_s=config.timing.poll_interval_s,
    )

    async def emit(event: DeviceEvent) -> None:
        await sm.handle_device_event(event)

    debouncer = Debouncer(
        emit,
        default_debounce_ms=config.timing.debounce_default_ms,
        per_class_debounce_ms=config.timing.debounce_per_class_ms,
    )

    udev_watcher = UdevWatcher(debouncer.feed, health)
    udev_watcher.start()
    liveness_watcher = HidrawLivenessWatcher(debouncer.feed)
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
            create_app(), host=config.wizard.bind_address, port=config.wizard.port, log_level="warning"
        )
        wizard_server = uvicorn.Server(uvicorn_config)
        wizard_server.install_signal_handlers = lambda: None  # daemon owns SIGTERM/SIGINT, not uvicorn
        wizard_task = asyncio.ensure_future(wizard_server.serve())
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

    logger.info("daemon: started, mode=%s configured=%s", sm.mode.value, config.configured)
    try:
        while not stop_event.is_set():
            health.heartbeat()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=HEARTBEAT_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass
    finally:
        logger.info("daemon: shutting down")
        udev_watcher.stop()
        await liveness_watcher.stop()
        await debouncer.aclose()
        await sm.aclose()
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
    parser = argparse.ArgumentParser(prog="jn-daemon")
    parser.add_argument("--doctor", action="store_true", help="one-shot self-test; exit non-zero on failure")
    parser.add_argument("--config", type=str, default=None)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level, format="%(asctime)s %(name)s %(levelname)s %(message)s")

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
