"""Embedded HTTP server, same process as the daemon (see daemon.py — the
wizard is meant to run alongside jn-daemon, sharing the same config/health
files). Binds to `config.wizard.bind_address` (127.0.0.1 by default) and
refuses to bind non-loopback without a password already configured — see
`auth.py` and plans/joystick-notify-v2.md's "Wizard network exposure and
auth" section.
"""
from __future__ import annotations

import logging
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from ..actions import audio as audio_actions
from ..actions import display as display_actions
from ..actions import launchers
from ..config import store as config_store
from ..config.schema import CustomCommand
from ..devices import cec as cec_discover
from ..health import read_snapshot
from . import auth as auth_module

logger = logging.getLogger(__name__)

PKG_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PKG_DIR / "templates"
STATIC_DIR = PKG_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_STATIC_PREFIX = "/static/"
_EXEMPT_PATHS = {"/setup-password"}


class AuthMiddleware(BaseHTTPMiddleware):
    """Enforces the plan's forced first-run flow: with no credentials
    stored, /setup-password is the only reachable page. With credentials
    stored, every non-static route requires HTTP Basic Auth.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith(_STATIC_PREFIX):
            return await call_next(request)

        creds = auth_module.load_credentials()
        if creds is None:
            if path in _EXEMPT_PATHS:
                return await call_next(request)
            return RedirectResponse("/setup-password")

        if path in _EXEMPT_PATHS:
            return RedirectResponse("/")

        header = request.headers.get("authorization")
        if not auth_module.check_basic_auth(header, creds):
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="joystick-notify"'})
        return await call_next(request)


async def index(request: Request):
    snapshot = read_snapshot()
    config = config_store.load()
    return templates.TemplateResponse(
        request, "index.html", {"snapshot": snapshot, "config": config}
    )


async def setup_password_get(request: Request):
    if auth_module.load_credentials() is not None:
        return RedirectResponse("/")
    return templates.TemplateResponse(
        request, "setup_password.html", {"error": None, "username": auth_module.ADMIN_USERNAME}
    )


async def setup_password_post(request: Request):
    if auth_module.load_credentials() is not None:
        return RedirectResponse("/")
    form = await request.form()
    password = str(form.get("password", ""))
    confirm = str(form.get("confirm", ""))
    error = None
    if len(password) < 8:
        error = "Password must be at least 8 characters."
    elif password != confirm:
        error = "Passwords do not match."
    if error:
        return templates.TemplateResponse(
            request, "setup_password.html", {"error": error, "username": auth_module.ADMIN_USERNAME}, status_code=400
        )
    creds = auth_module.create_credentials(auth_module.ADMIN_USERNAME, password)
    auth_module.save_credentials(creds)
    return RedirectResponse("/", status_code=303)


async def configure_get(request: Request):
    from dataclasses import asdict

    config = config_store.load()
    kjson = await display_actions.get_kscreen_json() or {"outputs": []}
    outputs = display_actions.parse_outputs(kjson)
    sinks = await audio_actions.list_sinks()
    detected_launchers = launchers.detect_launchers()
    cec_adapters = cec_discover.discover_adapters()

    # Best-effort topology auto-detect: only worth attempting once an
    # adapter actually exists, and never blocks the page on failure (see
    # get_topology()'s own docstring -- it returns [] rather than raising).
    cec_topology = []
    cec_suggested_standby_targets = ""
    if cec_adapters:
        adapter = config.cec.adapter or cec_adapters[0]
        cec_topology = await cec_discover.get_topology(adapter)
        if cec_topology:
            suggested = {0}  # TV is always logical address 0
            audio_target = cec_discover.find_audio_system_target(cec_topology)
            if audio_target is not None:
                suggested.add(audio_target.logical_address)
            cec_suggested_standby_targets = ",".join(str(a) for a in sorted(suggested))

    return templates.TemplateResponse(
        request,
        "configure.html",
        {
            "config": config,
            "outputs": outputs,
            "sinks": sinks,
            "launchers": detected_launchers,
            "cec_adapters": cec_adapters,
            "cec_topology": cec_topology,
            "cec_suggested_standby_targets": cec_suggested_standby_targets,
            "launch_presets": list(launchers.LAUNCH_PRESETS.keys()),
            # Alpine's x-data needs plain dicts to JSON-serialize via
            # Jinja's |tojson -- a raw list of CustomCommand dataclass
            # instances isn't JSON-serializable.
            "custom_commands_json": [asdict(c) for c in config.custom_commands],
        },
    )


async def configure_post(request: Request):
    form = await request.form()
    config = config_store.load()

    config.display.desk_port = str(form.get("desk_port", ""))
    config.display.couch_port = str(form.get("couch_port", ""))
    config.display.desk_mode = str(form.get("desk_mode", ""))
    config.display.couch_mode = str(form.get("couch_mode", ""))

    config.audio.desk_sink = str(form.get("desk_sink", ""))
    config.audio.couch_sink = str(form.get("couch_sink", ""))

    def _float(field: str, default: float, *, min_value: float) -> float:
        raw = str(form.get(field, "")).strip()
        try:
            value = float(raw)
        except ValueError:
            return default
        return value if value >= min_value else default

    def _positive_float(field: str, default: float) -> float:
        return _float(field, default, min_value=1e-9)  # ">0" without a magic epsilon comparison quirk

    def _nonneg_float(field: str, default: float) -> float:
        return _float(field, default, min_value=0.0)

    def _int(field: str, default: int, *, min_value: int) -> int:
        raw = str(form.get(field, "")).strip()
        try:
            value = int(raw)
        except ValueError:
            return default
        return value if value >= min_value else default

    def _positive_int(field: str, default: int) -> int:
        return _int(field, default, min_value=1)

    def _nonneg_int(field: str, default: int) -> int:
        return _int(field, default, min_value=0)

    def _int_list(field: str, default: list[int]) -> list[int]:
        raw = str(form.get(field, "")).strip()
        if not raw:
            return []
        values: list[int] = []
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                values.append(int(part))
            except ValueError:
                return default  # any malformed entry: reject the whole list, don't silently drop it
        return values

    config.cec.enabled = form.get("cec_enabled") == "on"
    config.cec.adapter = str(form.get("cec_adapter", ""))
    phys_addr = str(form.get("cec_active_source_phys_addr", "")).strip()
    config.cec.active_source_phys_addr = phys_addr
    config.cec.power_off_on_teardown = form.get("cec_power_off_on_teardown") == "on"
    config.cec.wake_delay_s = _nonneg_float("cec_wake_delay_s", config.cec.wake_delay_s)
    config.cec.active_source_retries = _nonneg_int("cec_active_source_retries", config.cec.active_source_retries)
    config.cec.active_source_retry_delay_s = _positive_float(
        "cec_active_source_retry_delay_s", config.cec.active_source_retry_delay_s
    )
    config.cec.standby_targets = _int_list("cec_standby_targets", config.cec.standby_targets)
    config.cec.standby_verify_attempts = _positive_int(
        "cec_standby_verify_attempts", config.cec.standby_verify_attempts
    )
    config.cec.standby_verify_delay_s = _positive_float(
        "cec_standby_verify_delay_s", config.cec.standby_verify_delay_s
    )

    config.on_connect.run = str(form.get("launch_preset", ""))

    names = form.getlist("custom_command_name") if hasattr(form, "getlist") else []
    values = form.getlist("custom_command_value") if hasattr(form, "getlist") else []
    config.custom_commands = [
        CustomCommand(name=n.strip(), command=v.strip())
        for n, v in zip(names, values)
        if n.strip() and v.strip()
    ]

    config.timing.disconnect_grace_s = _positive_float("disconnect_grace_s", config.timing.disconnect_grace_s)
    config.timing.launch_startup_grace_s = _positive_float("launch_startup_grace_s", config.timing.launch_startup_grace_s)
    config.timing.no_controller_timeout_s = _positive_float("no_controller_timeout_s", config.timing.no_controller_timeout_s)
    config.timing.poll_interval_s = _positive_float("poll_interval_s", config.timing.poll_interval_s)
    config.timing.debounce_default_ms = _nonneg_int("debounce_default_ms", config.timing.debounce_default_ms)

    config.screen_lock.enabled = form.get("screen_lock_enabled") == "on"
    config.screen_lock.hold_inhibit = form.get("screen_lock_hold_inhibit") == "on"

    config.shortcuts.exit_couch_enabled = form.get("exit_couch_enabled") == "on"
    config.shortcuts.exit_couch_hold_seconds = _positive_float(
        "exit_couch_hold_seconds", config.shortcuts.exit_couch_hold_seconds
    )

    # The advanced text field only overrides the checkbox when it holds a
    # genuinely specific address (e.g. a VPN/Tailscale interface IP) --
    # otherwise (blank, or still showing the generic 127.0.0.1/0.0.0.0 it
    # was populated with) the checkbox is the authoritative control, so
    # unchecking it to revoke LAN access always works even if the advanced
    # field wasn't touched.
    lan_access = form.get("wizard_lan_access") == "on"
    custom_bind = str(form.get("wizard_bind_address", "")).strip()
    if custom_bind and custom_bind not in ("127.0.0.1", "0.0.0.0"):
        config.wizard.bind_address = custom_bind
    else:
        config.wizard.bind_address = "0.0.0.0" if lan_access else "127.0.0.1"
    config.wizard.port = _positive_int("wizard_port", config.wizard.port)
    auth_module.validate_bind_address(config.wizard.bind_address, has_credentials=True)

    config.configured = True
    config_store.save(config)
    return RedirectResponse("/", status_code=303)


async def api_status(request: Request):
    snapshot = read_snapshot()
    if snapshot is None:
        return JSONResponse({"daemon_alive": False, "components": {}})
    body = {
        "daemon_alive": snapshot.daemon_alive,
        "overall": snapshot.overall.value if snapshot.daemon_alive else None,
        "components": {name: c.to_dict() for name, c in snapshot.components.items()},
    }
    return JSONResponse(body)


async def status_fragment(request: Request):
    """HTML fragment for htmx's periodic status poll on the dashboard —
    same read_snapshot() the tray uses, so the wizard and tray can never
    disagree about daemon health (plans/joystick-notify-v2.md, "Structured
    upstream status reporting")."""
    snapshot = read_snapshot()
    return templates.TemplateResponse(request, "_status_fragment.html", {"snapshot": snapshot})


async def status_detail_fragment(request: Request):
    snapshot = read_snapshot()
    return templates.TemplateResponse(request, "_status_detail_fragment.html", {"snapshot": snapshot})


async def events_fragment(request: Request):
    """"What just happened" view, newest first, for troubleshooting
    directly from the wizard instead of SSHing in to tail a log file.
    Defaults to the curated "main events" tier (controller connected,
    mode transitions, teardown decisions) -- the `level` query param
    (from the panel's dropdown) widens it to info/warning/error/debug.
    """
    import datetime

    from ..event_log import DEFAULT_LEVEL_FILTER, filter_events, read_events

    level = request.query_params.get("level", DEFAULT_LEVEL_FILTER)
    events = list(reversed(filter_events(read_events(), level)))[:50]
    rows = [
        {"time": datetime.datetime.fromtimestamp(e.timestamp).strftime("%H:%M:%S"), "level": e.level, "message": e.message}
        for e in events
    ]
    return templates.TemplateResponse(request, "_events_fragment.html", {"events": rows, "level": level})


async def api_events(request: Request):
    from ..event_log import DEFAULT_LEVEL_FILTER, filter_events, read_events

    level = request.query_params.get("level", DEFAULT_LEVEL_FILTER)
    events = list(reversed(filter_events(read_events(), level)))[:50]
    return JSONResponse({"events": [e.to_dict() for e in events], "level": level})


async def api_cec_test(request: Request):
    """Live-test button backing the CEC wizard step: sends wake +
    active-source to the requested phys-addr and lets the human confirm
    via the UI, per the plan's guided-picker flow. Deliberately does not
    persist anything — this is a "try it" action, not a save.
    """
    from ..actions import cec_control

    form = await request.form()
    adapter = str(form.get("adapter", "")) or None
    phys_addr = str(form.get("phys_addr", "")) or None
    if not phys_addr:
        return JSONResponse({"ok": False, "error": "phys_addr required"}, status_code=400)
    await cec_control.image_view_on(adapter)
    await cec_control.set_stream_path_and_active_source(adapter, phys_addr)
    return JSONResponse({"ok": True})


def create_app() -> Starlette:
    routes = [
        Route("/", index, methods=["GET"]),
        Route("/setup-password", setup_password_get, methods=["GET"]),
        Route("/setup-password", setup_password_post, methods=["POST"]),
        Route("/configure", configure_get, methods=["GET"]),
        Route("/configure", configure_post, methods=["POST"]),
        Route("/api/status", api_status, methods=["GET"]),
        Route("/api/cec/test", api_cec_test, methods=["POST"]),
        Route("/partials/status", status_fragment, methods=["GET"]),
        Route("/partials/status-detail", status_detail_fragment, methods=["GET"]),
        Route("/partials/events", events_fragment, methods=["GET"]),
        Route("/api/events", api_events, methods=["GET"]),
    ]
    app = Starlette(routes=routes, middleware=[Middleware(AuthMiddleware)])
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app


def run(bind_address: str | None = None, port: int | None = None) -> None:
    import uvicorn

    from ..session_env import ensure_session_environment

    ensure_session_environment()
    config = config_store.load()
    bind_address = bind_address or config.wizard.bind_address
    port = port or config.wizard.port

    creds = auth_module.load_credentials()
    auth_module.validate_bind_address(bind_address, has_credentials=creds is not None)

    app = create_app()
    logger.info("wizard: listening on %s:%d", bind_address, port)
    uvicorn.run(app, host=bind_address, port=port, log_level="info")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
