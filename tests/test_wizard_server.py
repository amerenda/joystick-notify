import base64

import pytest
from starlette.testclient import TestClient

from joystick_notify.wizard import auth as auth_module
from joystick_notify.wizard.server import create_app


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return tmp_path


@pytest.fixture
def client(isolated_config):
    app = create_app()
    return TestClient(app, follow_redirects=False)


def _basic(username: str, password: str) -> dict:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_index_redirects_to_setup_password_when_no_credentials(client):
    resp = client.get("/")
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/setup-password"


def test_setup_password_page_renders(client):
    resp = client.get("/setup-password")
    assert resp.status_code == 200
    assert "Set an admin password" in resp.text


def test_setup_password_page_displays_the_username(client):
    # Direct regression test: a real user reasonably assumed "admin" and
    # couldn't log in because the page never said what username to use
    # (it was silently the OS login name). The page must state it plainly.
    resp = client.get("/setup-password")
    assert auth_module.ADMIN_USERNAME in resp.text


def test_login_works_with_admin_username_after_setup(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    resp = client.get("/", headers=_basic("admin", "longenough1"))
    assert resp.status_code == 200
    assert auth_module.ADMIN_USERNAME == "admin"


def test_setup_password_rejects_short_password(client):
    resp = client.post("/setup-password", data={"password": "short", "confirm": "short"})
    assert resp.status_code == 400
    assert "at least 8 characters" in resp.text


def test_setup_password_rejects_mismatch(client):
    resp = client.post("/setup-password", data={"password": "longenough1", "confirm": "different1"})
    assert resp.status_code == 400
    assert "do not match" in resp.text


def test_setup_password_success_then_index_requires_auth(client):
    resp = client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"

    resp = client.get("/")
    assert resp.status_code == 401
    assert "WWW-Authenticate" in resp.headers


def test_index_with_correct_basic_auth_succeeds(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    resp = client.get("/", headers=_basic(auth_module.ADMIN_USERNAME, "longenough1"))
    assert resp.status_code == 200


def test_index_with_wrong_password_still_401(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    resp = client.get("/", headers=_basic(auth_module.ADMIN_USERNAME, "wrongpassword"))
    assert resp.status_code == 401


def test_setup_password_unreachable_once_configured(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    resp = client.get("/setup-password", headers=_basic(auth_module.ADMIN_USERNAME, "longenough1"))
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/"


def test_static_files_exempt_from_auth(client):
    resp = client.get("/static/vendor/htmx.min.js")
    assert resp.status_code == 200


def test_configure_get_requires_auth(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    resp = client.get("/configure")
    assert resp.status_code == 401


def test_configure_get_renders_all_sections(client, monkeypatch):
    # Advanced CEC tuning only renders once an adapter is detected (same
    # gating the existing phys-addr override/test button already used) --
    # this environment has no real /dev/cec* device, so fake one to
    # exercise that branch and confirm the new fields actually render with
    # their current config values, not just that the route returns 200.
    from joystick_notify.devices import cec as cec_discover

    monkeypatch.setattr(cec_discover, "discover_adapters", lambda: ["/dev/cec0"])

    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    resp = client.get("/configure", headers=_basic(auth_module.ADMIN_USERNAME, "longenough1"))

    assert resp.status_code == 200
    for needle in (
        "Advanced CEC tuning", "cec_wake_delay_s", "cec_standby_targets",
        "Advanced timing", "poll_interval_s", "debounce_default_ms",
        "Wizard access", "wizard_lan_access", "wizard_bind_address", "wizard_port",
        "Controller shortcut: exit couch mode", "exit_couch_hold_seconds",
        "custom_command_name", "custom_command_value", "+ Add command",
    ):
        assert needle in resp.text, f"missing from rendered page: {needle}"


def test_configure_get_renders_detected_cec_topology_and_suggestion(client, monkeypatch):
    from joystick_notify.devices import cec as cec_discover
    from joystick_notify.devices.cec import TopologyDevice

    monkeypatch.setattr(cec_discover, "discover_adapters", lambda: ["/dev/cec0"])

    async def fake_get_topology(adapter):
        return [
            TopologyDevice(logical_address=0, device_type="TV", osd_name="LG OLED"),
            TopologyDevice(logical_address=5, device_type="Audio System", osd_name="Receiver"),
        ]

    monkeypatch.setattr(cec_discover, "get_topology", fake_get_topology)

    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    resp = client.get("/configure", headers=_basic(auth_module.ADMIN_USERNAME, "longenough1"))

    assert resp.status_code == 200
    assert "LG OLED" in resp.text
    assert "Audio System" in resp.text
    assert "Use detected (0,5)" in resp.text


def test_configure_get_no_topology_section_when_get_topology_returns_empty(client, monkeypatch):
    # get_topology() failing/returning [] (no cec-ctl, timeout, adapter
    # unreachable) must not crash the page or show a bogus suggestion.
    from joystick_notify.devices import cec as cec_discover

    monkeypatch.setattr(cec_discover, "discover_adapters", lambda: ["/dev/cec0"])

    async def fake_get_topology(adapter):
        return []

    monkeypatch.setattr(cec_discover, "get_topology", fake_get_topology)

    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    resp = client.get("/configure", headers=_basic(auth_module.ADMIN_USERNAME, "longenough1"))

    assert resp.status_code == 200
    assert "Use detected" not in resp.text


def test_configure_get_renders_existing_custom_commands_into_the_alpine_data(client):
    # The Alpine x-data init blob is server-rendered via |tojson -- an
    # existing custom command must actually appear in the emitted JSON,
    # not just "the page returns 200."
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")
    client.post(
        "/configure",
        headers=auth_headers,
        data={
            "desk_port": "", "couch_port": "", "desk_sink": "", "couch_sink": "",
            "custom_command_name": "Play Portal 2",
            "custom_command_value": "steam steam://rungameid/620",
        },
    )

    resp = client.get("/configure", headers=auth_headers)
    assert resp.status_code == 200
    assert "Play Portal 2" in resp.text
    assert "steam://rungameid/620" in resp.text


def test_configure_post_saves_multiple_custom_commands(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    resp = client.post(
        "/configure",
        headers=auth_headers,
        data={
            "desk_port": "", "couch_port": "", "desk_sink": "", "couch_sink": "",
            "custom_command_name": ["Play Portal 2", "Play Half-Life 2"],
            "custom_command_value": ["steam steam://rungameid/620", "steam steam://rungameid/220"],
        },
    )
    assert resp.status_code == 303

    from joystick_notify.config import store as config_store

    saved = config_store.load()
    assert [(c.name, c.command) for c in saved.custom_commands] == [
        ("Play Portal 2", "steam steam://rungameid/620"),
        ("Play Half-Life 2", "steam steam://rungameid/220"),
    ]


def test_configure_post_drops_incomplete_custom_command_rows(client):
    # An "+ Add command" row left with only a name (or only a command)
    # typed in -- e.g. abandoned mid-edit -- must not be saved as a
    # half-empty entry.
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    client.post(
        "/configure",
        headers=auth_headers,
        data={
            "desk_port": "", "couch_port": "", "desk_sink": "", "couch_sink": "",
            "custom_command_name": ["Only a name", "", "Complete one"],
            "custom_command_value": ["", "only-a-command --flag", "real-command"],
        },
    )

    from joystick_notify.config import store as config_store

    saved = config_store.load()
    assert [(c.name, c.command) for c in saved.custom_commands] == [("Complete one", "real-command")]


def test_configure_post_editing_custom_commands_replaces_the_whole_list(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    client.post(
        "/configure",
        headers=auth_headers,
        data={
            "desk_port": "", "couch_port": "", "desk_sink": "", "couch_sink": "",
            "custom_command_name": "Old name", "custom_command_value": "old-command",
        },
    )
    client.post(
        "/configure",
        headers=auth_headers,
        data={
            "desk_port": "", "couch_port": "", "desk_sink": "", "couch_sink": "",
            "custom_command_name": "Renamed", "custom_command_value": "new-command",
        },
    )

    from joystick_notify.config import store as config_store

    saved = config_store.load()
    assert [(c.name, c.command) for c in saved.custom_commands] == [("Renamed", "new-command")]


def test_configure_post_clearing_all_custom_commands_saves_empty_list(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    client.post(
        "/configure",
        headers=auth_headers,
        data={
            "desk_port": "", "couch_port": "", "desk_sink": "", "couch_sink": "",
            "custom_command_name": "Temp", "custom_command_value": "temp-command",
        },
    )
    client.post(
        "/configure",
        headers=auth_headers,
        data={"desk_port": "", "couch_port": "", "desk_sink": "", "couch_sink": ""},
    )

    from joystick_notify.config import store as config_store

    saved = config_store.load()
    assert saved.custom_commands == []


def test_configure_post_saves_and_marks_configured(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    resp = client.post(
        "/configure",
        headers=auth_headers,
        data={
            "desk_port": "HDMI-A-2",
            "desk_mode": "2560x1440@144",
            "couch_port": "HDMI-A-1",
            "couch_mode": "3840x2160@60",
            "desk_sink": "",
            "couch_sink": "",
            "launch_preset": "steam-bigpicture",
        },
    )
    assert resp.status_code == 303

    from joystick_notify.config import store as config_store

    saved = config_store.load()
    assert saved.configured is True
    assert saved.display.desk_port == "HDMI-A-2"
    assert saved.on_connect.run == "steam-bigpicture"


def test_configure_post_saves_cec_power_off_and_timing_fields(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    resp = client.post(
        "/configure",
        headers=auth_headers,
        data={
            "desk_port": "",
            "couch_port": "",
            "desk_sink": "",
            "couch_sink": "",
            "cec_enabled": "on",
            "cec_power_off_on_teardown": "on",
            "disconnect_grace_s": "15",
            "launch_startup_grace_s": "20",
            "no_controller_timeout_s": "60",
        },
    )
    assert resp.status_code == 303

    from joystick_notify.config import store as config_store

    saved = config_store.load()
    assert saved.cec.power_off_on_teardown is True
    assert saved.timing.disconnect_grace_s == 15.0
    assert saved.timing.launch_startup_grace_s == 20.0
    assert saved.timing.no_controller_timeout_s == 60.0


def test_configure_post_unchecking_cec_power_off_saves_false(client):
    # Checkboxes only appear in form data when checked -- the absence of
    # "cec_power_off_on_teardown" must be read as False, not "leave as is."
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    client.post(
        "/configure",
        headers=auth_headers,
        data={"desk_port": "", "couch_port": "", "desk_sink": "", "couch_sink": "", "cec_power_off_on_teardown": "on"},
    )
    client.post(
        "/configure",
        headers=auth_headers,
        data={"desk_port": "", "couch_port": "", "desk_sink": "", "couch_sink": ""},
    )

    from joystick_notify.config import store as config_store

    saved = config_store.load()
    assert saved.cec.power_off_on_teardown is False


def test_configure_post_invalid_timing_value_falls_back_to_existing(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    resp = client.post(
        "/configure",
        headers=auth_headers,
        data={
            "desk_port": "", "couch_port": "", "desk_sink": "", "couch_sink": "",
            "disconnect_grace_s": "not-a-number",
        },
    )
    assert resp.status_code == 303

    from joystick_notify.config import store as config_store

    saved = config_store.load()
    assert saved.timing.disconnect_grace_s == 30.0  # schema default, unchanged


def test_configure_post_saves_shortcut_fields(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    resp = client.post(
        "/configure",
        headers=auth_headers,
        data={
            "desk_port": "", "couch_port": "", "desk_sink": "", "couch_sink": "",
            "exit_couch_enabled": "on",
            "exit_couch_hold_seconds": "5",
        },
    )
    assert resp.status_code == 303

    from joystick_notify.config import store as config_store

    saved = config_store.load()
    assert saved.shortcuts.exit_couch_enabled is True
    assert saved.shortcuts.exit_couch_hold_seconds == 5.0


def test_configure_post_unchecking_exit_couch_saves_false(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    # exit_couch_enabled defaults True -- explicitly omitting it (as an
    # unchecked checkbox would) must persist as False, not "leave as is."
    client.post(
        "/configure",
        headers=auth_headers,
        data={"desk_port": "", "couch_port": "", "desk_sink": "", "couch_sink": ""},
    )

    from joystick_notify.config import store as config_store

    saved = config_store.load()
    assert saved.shortcuts.exit_couch_enabled is False


def test_configure_post_saves_advanced_cec_tuning_fields(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    resp = client.post(
        "/configure",
        headers=auth_headers,
        data={
            "desk_port": "", "couch_port": "", "desk_sink": "", "couch_sink": "",
            "cec_wake_delay_s": "1.5",
            "cec_active_source_retries": "0",
            "cec_active_source_retry_delay_s": "6",
            "cec_standby_targets": "0, 5",
            "cec_standby_verify_attempts": "5",
            "cec_standby_verify_delay_s": "3",
        },
    )
    assert resp.status_code == 303

    from joystick_notify.config import store as config_store

    saved = config_store.load()
    assert saved.cec.wake_delay_s == 1.5
    assert saved.cec.active_source_retries == 0  # 0 is a legitimate value (no re-assert loop), not "invalid"
    assert saved.cec.active_source_retry_delay_s == 6.0
    assert saved.cec.standby_targets == [0, 5]
    assert saved.cec.standby_verify_attempts == 5
    assert saved.cec.standby_verify_delay_s == 3.0


def test_configure_post_malformed_standby_targets_falls_back_to_existing(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    resp = client.post(
        "/configure",
        headers=auth_headers,
        data={
            "desk_port": "", "couch_port": "", "desk_sink": "", "couch_sink": "",
            "cec_standby_targets": "0, not-a-number",
        },
    )
    assert resp.status_code == 303

    from joystick_notify.config import store as config_store

    saved = config_store.load()
    assert saved.cec.standby_targets == [0]  # schema default, unchanged by the malformed entry


def test_configure_post_clearing_standby_targets_saves_empty_list(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    client.post(
        "/configure",
        headers=auth_headers,
        data={"desk_port": "", "couch_port": "", "desk_sink": "", "couch_sink": "", "cec_standby_targets": ""},
    )

    from joystick_notify.config import store as config_store

    saved = config_store.load()
    assert saved.cec.standby_targets == []


def test_configure_post_saves_advanced_timing_fields(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    resp = client.post(
        "/configure",
        headers=auth_headers,
        data={
            "desk_port": "", "couch_port": "", "desk_sink": "", "couch_sink": "",
            "poll_interval_s": "1",
            "debounce_default_ms": "0",
        },
    )
    assert resp.status_code == 303

    from joystick_notify.config import store as config_store

    saved = config_store.load()
    assert saved.timing.poll_interval_s == 1.0
    assert saved.timing.debounce_default_ms == 0  # 0 is legitimate (no debounce), not "invalid"


def test_configure_post_saves_wizard_port(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    resp = client.post(
        "/configure",
        headers=auth_headers,
        data={"desk_port": "", "couch_port": "", "desk_sink": "", "couch_sink": "", "wizard_port": "9999"},
    )
    assert resp.status_code == 303

    from joystick_notify.config import store as config_store

    saved = config_store.load()
    assert saved.wizard.port == 9999
    assert saved.wizard.bind_address == "127.0.0.1"  # checkbox absent -> loopback-only, the safe default


def test_configure_post_lan_access_checkbox_binds_all_interfaces(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    resp = client.post(
        "/configure",
        headers=auth_headers,
        data={"desk_port": "", "couch_port": "", "desk_sink": "", "couch_sink": "", "wizard_lan_access": "on"},
    )
    assert resp.status_code == 303

    from joystick_notify.config import store as config_store

    saved = config_store.load()
    assert saved.wizard.bind_address == "0.0.0.0"


def test_configure_post_unchecking_lan_access_reverts_to_loopback(client):
    # The checkbox reflects "bind_address is non-loopback" on load and must
    # remain the authoritative control even after LAN access was previously
    # enabled -- unchecking it has to actually revoke access, not require
    # also clearing some other field.
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    client.post(
        "/configure",
        headers=auth_headers,
        data={"desk_port": "", "couch_port": "", "desk_sink": "", "couch_sink": "", "wizard_lan_access": "on"},
    )
    client.post(
        "/configure",
        headers=auth_headers,
        data={"desk_port": "", "couch_port": "", "desk_sink": "", "couch_sink": ""},
    )

    from joystick_notify.config import store as config_store

    saved = config_store.load()
    assert saved.wizard.bind_address == "127.0.0.1"


def test_configure_post_custom_bind_address_overrides_unchecked_checkbox(client):
    # The advanced field is for a power-user case (bind only a specific
    # VPN/Tailscale interface, not every interface) -- a genuinely specific
    # address must win regardless of the simple checkbox's state.
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    resp = client.post(
        "/configure",
        headers=auth_headers,
        data={
            "desk_port": "", "couch_port": "", "desk_sink": "", "couch_sink": "",
            "wizard_bind_address": "10.100.20.25",
        },
    )
    assert resp.status_code == 303

    from joystick_notify.config import store as config_store

    saved = config_store.load()
    assert saved.wizard.bind_address == "10.100.20.25"


def test_api_status_no_snapshot_yet(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    resp = client.get("/api/status", headers=_basic(auth_module.ADMIN_USERNAME, "longenough1"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["daemon_alive"] is False


def test_api_status_reflects_health_registry(client, isolated_config):
    from joystick_notify.health import Health

    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    health = Health()
    health.failed("deps", "cec-ctl not found")

    resp = client.get("/api/status", headers=_basic(auth_module.ADMIN_USERNAME, "longenough1"))
    body = resp.json()
    assert body["daemon_alive"] is True
    assert body["overall"] == "failed"
    assert body["components"]["deps"]["reason"] == "cec-ctl not found"


def test_api_events_empty_when_nothing_logged_yet(client, isolated_config):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    resp = client.get("/api/events", headers=_basic(auth_module.ADMIN_USERNAME, "longenough1"))
    assert resp.status_code == 200
    assert resp.json()["events"] == []


def test_api_events_reflects_event_log(client, isolated_config):
    import logging

    from joystick_notify.event_log import EventLogHandler

    logger = logging.getLogger("joystick_notify.wizard_test")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(EventLogHandler())
    logger.info("activity_gate[DEV1]: real activity observed, forwarding connect")

    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    # Plain INFO sits below the default "main" filter -- request the
    # everything-tier explicitly to verify the read+capture path itself.
    resp = client.get("/api/events?level=debug", headers=_basic(auth_module.ADMIN_USERNAME, "longenough1"))
    events = resp.json()["events"]
    assert len(events) == 1
    assert "forwarding connect" in events[0]["message"]


def test_api_events_default_level_hides_routine_info_shows_headline(client, isolated_config):
    import logging

    from joystick_notify.event_log import EventLogHandler, headline

    logger = logging.getLogger("joystick_notify.wizard_test2")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(EventLogHandler())
    logger.info("audio: default -> some-sink")
    headline(logger, "state_machine[dev1]: transitioned to couch")

    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    resp = client.get("/api/events", headers=_basic(auth_module.ADMIN_USERNAME, "longenough1"))
    events = resp.json()["events"]
    messages = [e["message"] for e in events]
    assert "state_machine[dev1]: transitioned to couch" in messages
    assert "audio: default -> some-sink" not in messages


def test_partials_events_renders_html(client, isolated_config):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    resp = client.get("/partials/events", headers=_basic(auth_module.ADMIN_USERNAME, "longenough1"))
    assert resp.status_code == 200
    assert "No events yet" in resp.text
