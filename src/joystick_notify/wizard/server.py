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
    config = config_store.load()
    kjson = await display_actions.get_kscreen_json() or {"outputs": []}
    outputs = display_actions.parse_outputs(kjson)
    sinks = await audio_actions.list_sinks()
    detected_launchers = launchers.detect_launchers()
    cec_adapters = cec_discover.discover_adapters()
    return templates.TemplateResponse(
        request,
        "configure.html",
        {
            "config": config,
            "outputs": outputs,
            "sinks": sinks,
            "launchers": detected_launchers,
            "cec_adapters": cec_adapters,
            "launch_presets": list(launchers.LAUNCH_PRESETS.keys()),
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

    config.cec.enabled = form.get("cec_enabled") == "on"
    config.cec.adapter = str(form.get("cec_adapter", ""))
    phys_addr = str(form.get("cec_active_source_phys_addr", "")).strip()
    config.cec.active_source_phys_addr = phys_addr

    config.on_connect.run = str(form.get("launch_preset", ""))
    power_on = form.getlist("power_on") if hasattr(form, "getlist") else []
    config.on_connect.power_on = list(power_on)

    config.screen_lock.enabled = form.get("screen_lock_enabled") == "on"
    config.screen_lock.hold_inhibit = form.get("screen_lock_hold_inhibit") == "on"

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
    """"What just happened" view — the same INFO-level events already
    written to the log (controller detected, waiting for input, CEC
    wake sent, display switched, ...), newest first, for troubleshooting
    directly from the wizard instead of SSHing in to tail a log file."""
    import datetime

    from ..event_log import read_events

    events = list(reversed(read_events()))[:50]
    rows = [
        {"time": datetime.datetime.fromtimestamp(e.timestamp).strftime("%H:%M:%S"), "message": e.message}
        for e in events
    ]
    return templates.TemplateResponse(request, "_events_fragment.html", {"events": rows})


async def api_events(request: Request):
    from ..event_log import read_events

    events = list(reversed(read_events()))[:50]
    return JSONResponse({"events": [e.to_dict() for e in events]})


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
