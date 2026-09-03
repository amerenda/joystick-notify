"""Embedded HTTP server, same process as the daemon (see daemon.py — the
wizard is meant to run alongside jn-daemon, sharing the same config/health
files). Binds to `config.wizard.bind_address` (127.0.0.1 by default) and
refuses to bind non-loopback without a password already configured — see
`auth.py` and plans/joystick-notify-v2.md's "Wizard network exposure and
auth" section.
"""
from __future__ import annotations

import asyncio
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
from ..actions import screen_lock as screen_lock_actions
from ..config import store as config_store
from ..config.schema import CustomCommand
from ..devices import cec as cec_discover
from ..health import Health, read_snapshot
from . import auth as auth_module

logger = logging.getLogger(__name__)

PKG_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PKG_DIR / "templates"
STATIC_DIR = PKG_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_STATIC_PREFIX = "/static/"
_EXEMPT_PATHS = {"/setup-password"}
# Routes a phone/automation client can hit with just the API token (see
# auth.py's ApiToken) instead of the admin password -- deliberately just
# this narrow set, not a blanket "/api/*" allowance, since the token is
# meant to be handed to a phone home-screen shortcut (or, for the screen/
# pair, Sunshine's stream hooks) and scoped to "what can this actually do
# if it leaks."
_API_TOKEN_PATHS = {
    "/api/mode/couch",
    "/api/mode/desk",
    "/api/screen/unlock",
    "/api/screen/lock",
    "/api/launch/steam-bigpicture",
}


class AuthMiddleware(BaseHTTPMiddleware):
    """Enforces the plan's forced first-run flow: with no credentials
    stored, /setup-password is the only reachable page. With credentials
    stored, every non-static route requires HTTP Basic Auth -- except the
    narrow `_API_TOKEN_PATHS` set, which also accepts a Bearer API token
    (see auth.py's ApiToken) so a phone app doesn't need the admin
    password.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith(_STATIC_PREFIX):
            return await call_next(request)

        if path in _API_TOKEN_PATHS:
            header = request.headers.get("authorization")
            if header and header.startswith("Bearer "):
                token = auth_module.load_api_token()
                if token is not None and auth_module.check_bearer_token(header, token):
                    return await call_next(request)
                return Response(status_code=401)
            # No bearer token presented -- fall through to normal basic-auth
            # handling below so the wizard's own status-page buttons (which
            # hit /mode/couch, /mode/desk, not these /api/ paths, but share
            # this same page-load auth) keep working unaffected.

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


def _live_state_machine(request: Request):
    """The wizard runs embedded in the same process as the daemon (see
    daemon.py's module docstring) and daemon.py stashes the live
    StateMachine on app.state.sm at startup -- but the standalone
    `python -m joystick_notify.wizard.server` entrypoint (used for
    UI-only manual testing) never sets it, so this is None in that case
    rather than an AttributeError.
    """
    return getattr(request.app.state, "sm", None)


def _live_health(request: Request) -> Health | None:
    """Same shape as `_live_state_machine` -- daemon.py also stashes its
    one shared Health registry on app.state.health at startup. This
    matters specifically because Health persists by overwriting its
    whole health.json snapshot from its own in-memory `_components`
    (see health.py's `_persist`) -- a route that constructed its own
    fresh `Health()` instead of reusing this one would wipe out every
    other component's reported status the moment it called `.ok()`.
    """
    return getattr(request.app.state, "health", None)


async def index(request: Request):
    snapshot = read_snapshot()
    config = config_store.load()
    sm = _live_state_machine(request)
    mode = sm.mode.value if sm is not None else None
    return templates.TemplateResponse(
        request,
        "index.html",
        {"snapshot": snapshot, "config": config, "mode": mode, "auto_switch_enabled": config.auto_switch_enabled},
    )


async def mode_couch(request: Request):
    sm = _live_state_machine(request)
    if sm is not None:
        await sm.force_enter_couch()
    return RedirectResponse("/", status_code=303)


async def mode_desk(request: Request):
    sm = _live_state_machine(request)
    if sm is not None:
        await sm.force_exit_to_desk()
    return RedirectResponse("/", status_code=303)


async def api_mode_couch(request: Request):
    sm = _live_state_machine(request)
    if sm is None:
        return JSONResponse({"ok": False, "error": "daemon state machine unavailable"}, status_code=503)
    await sm.force_enter_couch()
    return JSONResponse({"ok": True, "mode": sm.mode.value})


async def api_mode_desk(request: Request):
    sm = _live_state_machine(request)
    if sm is None:
        return JSONResponse({"ok": False, "error": "daemon state machine unavailable"}, status_code=503)
    await sm.force_exit_to_desk()
    return JSONResponse({"ok": True, "mode": sm.mode.value})


async def api_screen_unlock(request: Request):
    """Screen-unlock-only counterpart to api_mode_couch, for a caller
    (Sunshine's stream-start hook) that wants exactly screen_lock.py's
    unlock/disable-autolock/inhibit mechanism and nothing else -- not the
    full couch-mode transition (CEC TV/receiver wake, display output
    switch, audio switch, cursor hide, launcher). A remote/Deck stream
    has no reason to touch any of that; it captures the desktop and
    streams its own audio/video regardless of what's plugged into the
    TV. Calls screen_lock.activate_couch() directly -- the exact same
    function daemon.py's own activate_couch hook calls, just without
    going through the state machine's mode transition at all.

    The held ScreenSaver.Inhibit() cookie lives on app.state (this
    process is the one thing both calls share) rather than being
    returned to the caller -- api_screen_lock looks it up the same way.
    Idempotent against a caller that unlocks twice without an
    intervening lock (e.g. an overlapping second stream): the second
    call is a no-op rather than acquiring and leaking a second cookie.
    """
    health = _live_health(request)
    if health is None:
        return JSONResponse({"ok": False, "error": "daemon health unavailable"}, status_code=503)
    if getattr(request.app.state, "screen_unlock_held", False):
        return JSONResponse({"ok": True, "already_unlocked": True})
    config = config_store.load().screen_lock
    request.app.state.screen_unlock_cookie = await screen_lock_actions.activate_couch(config, health)
    request.app.state.screen_unlock_held = True
    return JSONResponse({"ok": True})


async def api_screen_lock(request: Request):
    health = _live_health(request)
    if health is None:
        return JSONResponse({"ok": False, "error": "daemon health unavailable"}, status_code=503)
    if not getattr(request.app.state, "screen_unlock_held", False):
        return JSONResponse({"ok": True, "already_locked": True})
    config = config_store.load().screen_lock
    cookie = getattr(request.app.state, "screen_unlock_cookie", None)
    await screen_lock_actions.activate_desk(config, health, cookie)
    request.app.state.screen_unlock_cookie = None
    request.app.state.screen_unlock_held = False
    return JSONResponse({"ok": True})


async def api_launch_steam_bigpicture(request: Request):
    """One-shot counterpart used by Sunshine's "Steam Big Picture" app
    entry specifically (not the global unlock hook -- see
    api_screen_unlock). Calls launchers.launch_steam_bigpicture()
    directly, the exact same function config.on_connect="steam-bigpicture"
    calls for a controller-triggered couch entry -- deliberately reused
    rather than having Sunshine's apps.json run `steam -gamepadui`
    itself, which would silently drop the shutdown-existing-instance-
    first handling that function does specifically to avoid a real,
    already-fixed race (see its own docstring, 2026-08-22: a stale Big
    Picture window surviving a mode switch could lose signal entirely).
    No held state / no counterpart "undo" call needed -- this doesn't
    hold anything across the stream's lifetime.
    """
    await launchers.launch_steam_bigpicture()
    return JSONResponse({"ok": True})


async def api_restart(request: Request):
    """Fire-and-forget `systemctl --user restart <unit>` for the "Restart
    daemon" button. Deliberately doesn't await the subprocess: this
    process (the daemon, with the wizard embedded in it) IS the thing
    being restarted, so waiting for the restart to finish would mean
    waiting for this very request handler to be killed out from under
    itself. The JSONResponse below only needs to reach uvicorn's write
    buffer before systemd's SIGTERM arrives, which the launch alone
    (before the service manager even processes the command) comfortably
    outpaces.

    Guards the configured unit name against anything other than a
    joystick-notify service -- config.wizard.systemd_service_name is
    user-editable (needed since a dev/test install like
    joystick-notify-v2-test.service is a different unit than
    production), so a typo here must not become "restart some unrelated
    systemd unit."
    """
    config = config_store.load()
    service = config.wizard.systemd_service_name
    if not service.startswith("joystick-notify"):
        return JSONResponse(
            {"ok": False, "error": f"refusing to restart unexpected service name {service!r}"}, status_code=400
        )
    try:
        await asyncio.create_subprocess_exec(
            "systemctl", "--user", "restart", service,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return JSONResponse({"ok": False, "error": "systemctl not found"}, status_code=500)
    return JSONResponse({"ok": True, "service": service})


async def autoswitch_enable(request: Request):
    config = config_store.load()
    config.auto_switch_enabled = True
    config_store.save(config)
    return RedirectResponse("/", status_code=303)


async def autoswitch_disable(request: Request):
    config = config_store.load()
    config.auto_switch_enabled = False
    config_store.save(config)
    return RedirectResponse("/", status_code=303)


async def api_autoswitch_get(request: Request):
    config = config_store.load()
    return JSONResponse({"ok": True, "enabled": config.auto_switch_enabled})


async def api_autoswitch_set(request: Request):
    """Same config.toml read-modify-write the tray's right-click toggle
    uses directly (see daemon.py's _forward_to_state_machine docstring for
    why config.toml, not a shared in-memory flag, is the single source of
    truth both processes converge on) -- this just gives the wizard/a
    script an HTTP path to the same effect.
    """
    config = config_store.load()
    body = await request.json()
    config.auto_switch_enabled = bool(body.get("enabled"))
    config_store.save(config)
    return JSONResponse({"ok": True, "enabled": config.auto_switch_enabled})


async def token_generate(request: Request):
    token, api_token = auth_module.generate_api_token()
    auth_module.save_api_token(api_token)
    return templates.TemplateResponse(request, "token_created.html", {"token": token})


async def token_revoke(request: Request):
    auth_module.delete_api_token()
    return RedirectResponse("/configure", status_code=303)


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
    api_token = auth_module.load_api_token()

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
            # Alpine's x-data needs plain dicts to JSON-serialize via
            # Jinja's |tojson -- a raw list of CustomCommand dataclass
            # instances isn't JSON-serializable.
            "custom_commands_json": [asdict(c) for c in config.custom_commands],
            "api_token": api_token,
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
    config.on_connect.teardown_command = str(form.get("teardown_command", "")).strip()

    names = form.getlist("custom_command_name") if hasattr(form, "getlist") else []
    values = form.getlist("custom_command_value") if hasattr(form, "getlist") else []
    config.custom_commands = [
        CustomCommand(name=n.strip(), command=v.strip())
        for n, v in zip(names, values)
        if n.strip() and v.strip()
    ]

    config.timing.disconnect_grace_s = _positive_float("disconnect_grace_s", config.timing.disconnect_grace_s)
    config.timing.launch_startup_grace_s = _positive_float("launch_startup_grace_s", config.timing.launch_startup_grace_s)
    config.timing.poll_interval_s = _positive_float("poll_interval_s", config.timing.poll_interval_s)
    config.timing.debounce_default_ms = _nonneg_int("debounce_default_ms", config.timing.debounce_default_ms)

    config.idle.wait_for_game = form.get("idle_wait_for_game") == "on"
    config.idle.screensaver_enabled = form.get("idle_screensaver_enabled") == "on"
    config.idle.idle_after_s = _positive_float("idle_after_s", config.idle.idle_after_s)

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

    # Same posture as the service-name guard on /api/restart: an editable
    # free-text field must not be able to point the "Restart daemon"
    # button at an arbitrary systemd unit, so a malformed/unrelated value
    # here is simply ignored rather than saved.
    systemd_service_name = str(form.get("systemd_service_name", "")).strip()
    if systemd_service_name.startswith("joystick-notify"):
        config.wizard.systemd_service_name = systemd_service_name

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


async def api_cec_topology(request: Request):
    """On-demand CEC topology scan for the wizard's "Scan for devices"
    button. Deliberately NOT run automatically on every /configure page
    load -- confirmed live 2026-08-22 that `cec-ctl -S` alone takes ~7s,
    which made the whole page hang that long on every single load. A
    real bus scan belongs behind an explicit action, same as the existing
    "Test wake + switch input" button, not blocking page render.
    """
    from ..config import store as config_store
    from ..devices import cec as cec_discover

    form = await request.form()
    adapter = str(form.get("adapter", "")) or None
    if not adapter:
        adapters = cec_discover.discover_adapters()
        if not adapters:
            return JSONResponse({"ok": False, "error": "no CEC adapter found"}, status_code=400)
        config = config_store.load()
        adapter = config.cec.adapter or adapters[0]

    topology = await cec_discover.get_topology(adapter)
    suggested = ""
    if topology:
        targets = {0}  # TV is always logical address 0
        audio_target = cec_discover.find_audio_system_target(topology)
        if audio_target is not None:
            targets.add(audio_target.logical_address)
        suggested = ",".join(str(a) for a in sorted(targets))

    return JSONResponse({
        "ok": True,
        "devices": [
            {"logical_address": d.logical_address, "device_type": d.device_type, "osd_name": d.osd_name}
            for d in topology
        ],
        "suggested_standby_targets": suggested,
    })


def create_app(sm=None, health: Health | None = None) -> Starlette:
    """`sm` is the live daemon StateMachine when the wizard runs embedded
    in run_daemon() (see daemon.py) -- stashed on app.state so the
    mode-switch routes below can call into it directly, one process, no
    IPC. `health` is that same run's shared Health registry, needed by
    api_screen_unlock/api_screen_lock (see _live_health's docstring for
    why reusing this one specific instance matters). Both None for the
    standalone `python -m joystick_notify.wizard.server` entrypoint
    (UI-only manual testing); those routes then report unavailable
    rather than erroring.
    """
    routes = [
        Route("/", index, methods=["GET"]),
        Route("/setup-password", setup_password_get, methods=["GET"]),
        Route("/setup-password", setup_password_post, methods=["POST"]),
        Route("/configure", configure_get, methods=["GET"]),
        Route("/configure", configure_post, methods=["POST"]),
        Route("/mode/couch", mode_couch, methods=["POST"]),
        Route("/mode/desk", mode_desk, methods=["POST"]),
        Route("/autoswitch/enable", autoswitch_enable, methods=["POST"]),
        Route("/autoswitch/disable", autoswitch_disable, methods=["POST"]),
        Route("/token/generate", token_generate, methods=["POST"]),
        Route("/token/revoke", token_revoke, methods=["POST"]),
        Route("/api/status", api_status, methods=["GET"]),
        Route("/api/cec/test", api_cec_test, methods=["POST"]),
        Route("/api/cec/topology", api_cec_topology, methods=["POST"]),
        Route("/api/mode/couch", api_mode_couch, methods=["POST"]),
        Route("/api/mode/desk", api_mode_desk, methods=["POST"]),
        Route("/api/screen/unlock", api_screen_unlock, methods=["POST"]),
        Route("/api/screen/lock", api_screen_lock, methods=["POST"]),
        Route("/api/launch/steam-bigpicture", api_launch_steam_bigpicture, methods=["POST"]),
        Route("/api/autoswitch", api_autoswitch_get, methods=["GET"]),
        Route("/api/autoswitch", api_autoswitch_set, methods=["POST"]),
        Route("/api/restart", api_restart, methods=["POST"]),
        Route("/partials/status", status_fragment, methods=["GET"]),
        Route("/partials/status-detail", status_detail_fragment, methods=["GET"]),
        Route("/partials/events", events_fragment, methods=["GET"]),
        Route("/api/events", api_events, methods=["GET"]),
    ]
    app = Starlette(routes=routes, middleware=[Middleware(AuthMiddleware)])
    app.state.sm = sm
    app.state.health = health
    app.state.screen_unlock_cookie = None
    app.state.screen_unlock_held = False
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
