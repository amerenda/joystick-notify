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
        "Advanced CEC Tuning", "cec_wake_delay_s", "cec_standby_targets",
        "Advanced Timing", "poll_interval_s", "debounce_default_ms",
        "Wizard Access", "wizard_lan_access", "wizard_bind_address", "wizard_port",
        "Controller Shortcut: Exit Couch Mode", "exit_couch_hold_seconds",
        "custom_command_name", "custom_command_value", "+ Add Command",
        "idle_wait_for_game", "idle_screensaver_enabled", "idle_after_s",
        "systemd_service_name", "Restart Daemon", "API Access",
    ):
        assert needle in resp.text, f"missing from rendered page: {needle}"


def test_configure_get_alpine_data_attributes_are_not_broken_by_tojson_quotes(client):
    # Direct regression test for a real bug: Jinja's |tojson output
    # contains literal double quotes (it's JSON), so embedding it inside
    # a DOUBLE-quoted HTML attribute (x-data="...{{ x|tojson }}...")
    # prematurely closes the attribute at the first embedded quote,
    # corrupting the tag and everything the browser parses after it on
    # the page -- reported live as "edit config does not work either."
    # Must use single-quoted attributes instead, since JSON never
    # contains a literal single quote.
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    # A non-empty custom command and a non-empty on_connect.run are the
    # exact conditions that broke this -- an empty list/string happens to
    # produce no embedded quotes and would mask the bug.
    client.post(
        "/configure",
        headers=auth_headers,
        data={
            "desk_port": "", "couch_port": "", "desk_sink": "", "couch_sink": "",
            "launch_preset": "steam-bigpicture",
            "custom_command_name": "Play Portal 2",
            "custom_command_value": "steam steam://rungameid/620",
        },
    )

    resp = client.get("/configure", headers=auth_headers)
    assert resp.status_code == 200
    assert "x-data='customCommands(" in resp.text
    assert 'x-data="customCommands(' not in resp.text
    assert ":selected='cmd.command === " in resp.text
    assert ':selected="cmd.command === ' not in resp.text


def test_configure_get_does_not_scan_cec_topology_on_page_load(client, monkeypatch):
    # Direct regression test: get_topology() (which shells out to
    # `cec-ctl -S`, confirmed live 2026-08-22 to take ~7s on its own) used
    # to run synchronously on every /configure GET, making the whole page
    # hang that long every single load. It must now only run on-demand,
    # from the "Scan for devices" button's /api/cec/topology call -- never
    # as a side effect of just loading the page.
    from joystick_notify.devices import cec as cec_discover

    monkeypatch.setattr(cec_discover, "discover_adapters", lambda: ["/dev/cec0"])
    calls = []

    async def fake_get_topology(adapter):
        calls.append(adapter)
        return []

    monkeypatch.setattr(cec_discover, "get_topology", fake_get_topology)

    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    resp = client.get("/configure", headers=_basic(auth_module.ADMIN_USERNAME, "longenough1"))

    assert resp.status_code == 200
    assert calls == []
    assert "Scan for Devices" in resp.text


def test_api_cec_topology_returns_devices_and_suggestion(client, monkeypatch):
    from joystick_notify.devices import cec as cec_discover
    from joystick_notify.devices.cec import TopologyDevice

    async def fake_get_topology(adapter):
        assert adapter == "/dev/cec0"
        return [
            TopologyDevice(logical_address=0, device_type="TV", osd_name="LG OLED"),
            TopologyDevice(logical_address=5, device_type="Audio System", osd_name="Receiver"),
        ]

    monkeypatch.setattr(cec_discover, "get_topology", fake_get_topology)

    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")
    resp = client.post("/api/cec/topology", headers=auth_headers, data={"adapter": "/dev/cec0"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["suggested_standby_targets"] == "0,5"
    assert body["devices"][0]["osd_name"] == "LG OLED"


def test_api_cec_topology_falls_back_to_config_adapter_when_not_specified(client, monkeypatch):
    from joystick_notify.devices import cec as cec_discover

    monkeypatch.setattr(cec_discover, "discover_adapters", lambda: ["/dev/cec0"])
    seen = []

    async def fake_get_topology(adapter):
        seen.append(adapter)
        return []

    monkeypatch.setattr(cec_discover, "get_topology", fake_get_topology)

    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")
    resp = client.post("/api/cec/topology", headers=auth_headers, data={})

    assert resp.status_code == 200
    assert seen == ["/dev/cec0"]


def test_api_cec_topology_no_adapter_available_returns_error(client, monkeypatch):
    from joystick_notify.devices import cec as cec_discover

    monkeypatch.setattr(cec_discover, "discover_adapters", lambda: [])

    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")
    resp = client.post("/api/cec/topology", headers=auth_headers, data={})

    assert resp.status_code == 400
    assert resp.json()["ok"] is False


def test_api_cec_topology_no_devices_found(client, monkeypatch):
    from joystick_notify.devices import cec as cec_discover

    async def fake_get_topology(adapter):
        return []

    monkeypatch.setattr(cec_discover, "get_topology", fake_get_topology)

    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")
    resp = client.post("/api/cec/topology", headers=auth_headers, data={"adapter": "/dev/cec0"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["devices"] == []
    assert body["suggested_standby_targets"] == ""


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
            "idle_after_s": "60",
        },
    )
    assert resp.status_code == 303

    from joystick_notify.config import store as config_store

    saved = config_store.load()
    assert saved.cec.power_off_on_teardown is True
    assert saved.timing.disconnect_grace_s == 15.0
    assert saved.timing.launch_startup_grace_s == 20.0
    assert saved.idle.idle_after_s == 60.0


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


def test_configure_post_saves_idle_config(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    resp = client.post(
        "/configure",
        headers=auth_headers,
        data={
            "desk_port": "", "couch_port": "", "desk_sink": "", "couch_sink": "",
            "idle_wait_for_game": "on",
            "idle_screensaver_enabled": "on",
            "idle_after_s": "90",
        },
    )
    assert resp.status_code == 303

    from joystick_notify.config import store as config_store

    saved = config_store.load()
    assert saved.idle.wait_for_game is True
    assert saved.idle.screensaver_enabled is True
    assert saved.idle.idle_after_s == 90.0


def test_configure_post_unchecking_idle_toggles_saves_false(client):
    # Both idle.wait_for_game and idle.screensaver_enabled default True --
    # unchecking them (absent from form data) must persist as False.
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    client.post(
        "/configure",
        headers=auth_headers,
        data={"desk_port": "", "couch_port": "", "desk_sink": "", "couch_sink": ""},
    )

    from joystick_notify.config import store as config_store

    saved = config_store.load()
    assert saved.idle.wait_for_game is False
    assert saved.idle.screensaver_enabled is False


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


def test_configure_post_saves_systemd_service_name(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    resp = client.post(
        "/configure",
        headers=auth_headers,
        data={
            "desk_port": "", "couch_port": "", "desk_sink": "", "couch_sink": "",
            "systemd_service_name": "joystick-notify-v2-test.service",
        },
    )
    assert resp.status_code == 303

    from joystick_notify.config import store as config_store

    saved = config_store.load()
    assert saved.wizard.systemd_service_name == "joystick-notify-v2-test.service"


def test_configure_post_rejects_systemd_service_name_not_starting_with_joystick_notify(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    resp = client.post(
        "/configure",
        headers=auth_headers,
        data={
            "desk_port": "", "couch_port": "", "desk_sink": "", "couch_sink": "",
            "systemd_service_name": "some-other-service.service",
        },
    )
    assert resp.status_code == 303

    from joystick_notify.config import store as config_store

    saved = config_store.load()
    assert saved.wizard.systemd_service_name == "joystick-notify.service"  # schema default, unchanged


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


class _FakeStateMachine:
    """Minimal stand-in for StateMachine -- these tests exercise the
    wizard's HTTP wiring (app.state.sm, route dispatch, auth), not the
    real state machine's transition logic (already covered directly in
    test_state_machine.py)."""

    def __init__(self):
        from joystick_notify.state_machine import Mode

        self.mode = Mode.DESK
        self.couch_calls = 0
        self.desk_calls = 0

    async def force_enter_couch(self):
        from joystick_notify.state_machine import Mode

        self.couch_calls += 1
        self.mode = Mode.COUCH

    async def force_exit_to_desk(self):
        from joystick_notify.state_machine import Mode

        self.desk_calls += 1
        self.mode = Mode.DESK


def test_index_shows_current_mode_and_switch_buttons(isolated_config):
    sm = _FakeStateMachine()
    app = create_app(sm)
    client = TestClient(app, follow_redirects=False)
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    resp = client.get("/", headers=_basic(auth_module.ADMIN_USERNAME, "longenough1"))
    assert resp.status_code == 200
    assert "desk" in resp.text
    assert 'action="/mode/couch"' in resp.text
    assert 'action="/mode/desk"' in resp.text


def test_mode_couch_route_calls_state_machine_and_redirects(isolated_config):
    sm = _FakeStateMachine()
    app = create_app(sm)
    client = TestClient(app, follow_redirects=False)
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    resp = client.post("/mode/couch", headers=auth_headers)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert sm.couch_calls == 1


def test_mode_desk_route_calls_state_machine_and_redirects(isolated_config):
    sm = _FakeStateMachine()
    app = create_app(sm)
    client = TestClient(app, follow_redirects=False)
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    resp = client.post("/mode/desk", headers=auth_headers)
    assert resp.status_code == 303
    assert sm.desk_calls == 1


def test_mode_routes_require_auth(isolated_config):
    sm = _FakeStateMachine()
    app = create_app(sm)
    client = TestClient(app, follow_redirects=False)
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})

    resp = client.post("/mode/couch")
    assert resp.status_code == 401
    assert sm.couch_calls == 0


def test_api_mode_couch_without_live_state_machine_returns_503(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")
    resp = client.post("/api/mode/couch", headers=auth_headers)
    assert resp.status_code == 503


def test_api_mode_couch_with_basic_auth_succeeds(isolated_config):
    sm = _FakeStateMachine()
    app = create_app(sm)
    client = TestClient(app, follow_redirects=False)
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    resp = client.post("/api/mode/couch", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "mode": "couch"}
    assert sm.couch_calls == 1


def test_api_mode_requires_some_form_of_auth(isolated_config):
    sm = _FakeStateMachine()
    app = create_app(sm)
    client = TestClient(app, follow_redirects=False)
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})

    resp = client.post("/api/mode/couch")
    assert resp.status_code == 401
    assert sm.couch_calls == 0


def test_api_mode_couch_with_valid_bearer_token_succeeds_without_password(isolated_config):
    # The whole point of the API token: a phone client authenticates with
    # just the token, never the admin password.
    sm = _FakeStateMachine()
    app = create_app(sm)
    client = TestClient(app, follow_redirects=False)
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})

    token, api_token = auth_module.generate_api_token()
    auth_module.save_api_token(api_token)

    resp = client.post("/api/mode/couch", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["mode"] == "couch"
    assert sm.couch_calls == 1


def test_api_mode_desk_with_valid_bearer_token_succeeds(isolated_config):
    sm = _FakeStateMachine()
    app = create_app(sm)
    client = TestClient(app, follow_redirects=False)
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})

    token, api_token = auth_module.generate_api_token()
    auth_module.save_api_token(api_token)

    resp = client.post("/api/mode/desk", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert sm.desk_calls == 1


def test_api_mode_with_wrong_bearer_token_rejected(isolated_config):
    sm = _FakeStateMachine()
    app = create_app(sm)
    client = TestClient(app, follow_redirects=False)
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})

    token, api_token = auth_module.generate_api_token()
    auth_module.save_api_token(api_token)

    resp = client.post("/api/mode/couch", headers={"Authorization": "Bearer not-the-real-token"})
    assert resp.status_code == 401
    assert sm.couch_calls == 0


def test_api_mode_bearer_token_scoped_to_mode_routes_only(isolated_config):
    # Direct regression test for the deliberate scoping decision: a leaked
    # API token must not double as a general admin credential -- it must
    # not grant access to e.g. /configure.
    app = create_app(_FakeStateMachine())
    client = TestClient(app, follow_redirects=False)
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})

    token, api_token = auth_module.generate_api_token()
    auth_module.save_api_token(api_token)

    resp = client.get("/configure", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_api_screen_unlock_without_live_health_returns_503(client):
    # create_app() with no health arg -- e.g. the standalone UI-testing
    # entrypoint -- must report unavailable, not raise.
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")
    resp = client.post("/api/screen/unlock", headers=auth_headers)
    assert resp.status_code == 503


def test_api_screen_unlock_and_lock_call_screen_lock_actions_with_the_cookie(isolated_config, monkeypatch):
    from joystick_notify.health import Health
    from joystick_notify.wizard import server as server_module

    calls = {"couch": 0, "desk": []}

    async def fake_activate_couch(config, health):
        calls["couch"] += 1
        return "fake-cookie-123"

    async def fake_activate_desk(config, health, cookie):
        calls["desk"].append(cookie)

    monkeypatch.setattr(server_module.screen_lock_actions, "activate_couch", fake_activate_couch)
    monkeypatch.setattr(server_module.screen_lock_actions, "activate_desk", fake_activate_desk)

    app = create_app(health=Health())
    client = TestClient(app, follow_redirects=False)
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    token, api_token = auth_module.generate_api_token()
    auth_module.save_api_token(api_token)
    headers = {"Authorization": f"Bearer {token}"}

    unlock_resp = client.post("/api/screen/unlock", headers=headers)
    assert unlock_resp.status_code == 200
    assert unlock_resp.json() == {"ok": True}
    assert calls["couch"] == 1

    lock_resp = client.post("/api/screen/lock", headers=headers)
    assert lock_resp.status_code == 200
    assert calls["desk"] == ["fake-cookie-123"]  # the couch call's own cookie, round-tripped


def test_api_screen_unlock_twice_does_not_leak_a_second_cookie(isolated_config, monkeypatch):
    # Regression guard: an overlapping second stream calling unlock again
    # before the first one's lock must not acquire (and leak) a second
    # ScreenSaver.Inhibit() cookie.
    from joystick_notify.health import Health
    from joystick_notify.wizard import server as server_module

    calls = {"couch": 0}

    async def fake_activate_couch(config, health):
        calls["couch"] += 1
        return "cookie"

    monkeypatch.setattr(server_module.screen_lock_actions, "activate_couch", fake_activate_couch)

    app = create_app(health=Health())
    client = TestClient(app, follow_redirects=False)
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    token, api_token = auth_module.generate_api_token()
    auth_module.save_api_token(api_token)
    headers = {"Authorization": f"Bearer {token}"}

    first = client.post("/api/screen/unlock", headers=headers)
    second = client.post("/api/screen/unlock", headers=headers)

    assert first.json() == {"ok": True}
    assert second.json() == {"ok": True, "already_unlocked": True}
    assert calls["couch"] == 1


def test_api_screen_lock_without_prior_unlock_is_a_noop(isolated_config):
    from joystick_notify.health import Health

    app = create_app(health=Health())
    client = TestClient(app, follow_redirects=False)
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    token, api_token = auth_module.generate_api_token()
    auth_module.save_api_token(api_token)

    resp = client.post("/api/screen/lock", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "already_locked": True}


def test_api_screen_unlock_lock_bearer_token_scoped_correctly(isolated_config):
    # Same scoping guarantee as the mode-switch pair -- these two are
    # meant for Sunshine's hooks specifically, not a blanket /api/* grant.
    from joystick_notify.health import Health

    app = create_app(health=Health())
    client = TestClient(app, follow_redirects=False)
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    token, api_token = auth_module.generate_api_token()
    auth_module.save_api_token(api_token)

    resp = client.get("/configure", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_api_launch_steam_bigpicture_calls_the_launcher_function(isolated_config, monkeypatch):
    from joystick_notify.wizard import server as server_module

    calls = {"launched": 0}

    async def fake_launch():
        calls["launched"] += 1

    monkeypatch.setattr(server_module.launchers, "launch_steam_bigpicture", fake_launch)

    app = create_app()
    client = TestClient(app, follow_redirects=False)
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    token, api_token = auth_module.generate_api_token()
    auth_module.save_api_token(api_token)

    resp = client.post("/api/launch/steam-bigpicture", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert calls["launched"] == 1


def test_api_launch_steam_bigpicture_bearer_token_scoped_correctly(isolated_config):
    app = create_app()
    client = TestClient(app, follow_redirects=False)
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    token, api_token = auth_module.generate_api_token()
    auth_module.save_api_token(api_token)

    resp = client.get("/configure", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_token_generate_shows_token_once(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    resp = client.post("/token/generate", headers=auth_headers)
    assert resp.status_code == 200
    assert "API token created" in resp.text

    stored = auth_module.load_api_token()
    assert stored is not None


def test_token_revoke_removes_stored_token(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    client.post("/token/generate", headers=auth_headers)
    assert auth_module.load_api_token() is not None

    resp = client.post("/token/revoke", headers=auth_headers)
    assert resp.status_code == 303
    assert auth_module.load_api_token() is None


def test_configure_get_reflects_token_status(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    resp = client.get("/configure", headers=auth_headers)
    assert "not configured" in resp.text
    assert "Generate Token" in resp.text

    client.post("/token/generate", headers=auth_headers)
    resp = client.get("/configure", headers=auth_headers)
    assert "Revoke Token" in resp.text


def test_api_restart_rejects_unexpected_service_name(client, monkeypatch):
    from joystick_notify.config import store as config_store

    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    config = config_store.load()
    config.wizard.systemd_service_name = "some-other-service.service"
    config_store.save(config)

    resp = client.post("/api/restart", headers=auth_headers)
    assert resp.status_code == 400
    assert resp.json()["ok"] is False


def test_api_restart_launches_systemctl_for_configured_service(client, monkeypatch):
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)

        class _P:
            returncode = 0

        return _P()

    import asyncio as asyncio_module

    monkeypatch.setattr(asyncio_module, "create_subprocess_exec", fake_exec)

    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    resp = client.post("/api/restart", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["service"] == "joystick-notify.service"
    assert calls == [("systemctl", "--user", "restart", "joystick-notify.service")]


def test_configure_post_saves_teardown_command(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    resp = client.post(
        "/configure",
        headers=auth_headers,
        data={
            "desk_port": "", "couch_port": "", "desk_sink": "", "couch_sink": "",
            "teardown_command": "steam -shutdown",
        },
    )
    assert resp.status_code == 303

    from joystick_notify.config import store as config_store

    assert config_store.load().on_connect.teardown_command == "steam -shutdown"


def test_configure_post_clearing_teardown_command_saves_empty(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    client.post(
        "/configure",
        headers=auth_headers,
        data={
            "desk_port": "", "couch_port": "", "desk_sink": "", "couch_sink": "",
            "teardown_command": "some-command",
        },
    )
    client.post(
        "/configure",
        headers=auth_headers,
        data={"desk_port": "", "couch_port": "", "desk_sink": "", "couch_sink": ""},
    )

    from joystick_notify.config import store as config_store

    assert config_store.load().on_connect.teardown_command == ""


def test_configure_get_renders_tab_bar(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    resp = client.get("/configure", headers=_basic(auth_module.ADMIN_USERNAME, "longenough1"))
    assert resp.status_code == 200
    for tab_label in ("Display", "Audio", "CEC", "Launch", "Controllers", "Daemon Settings"):
        assert tab_label in resp.text


def test_index_shows_auto_switch_state_and_toggle_buttons(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")
    resp = client.get("/", headers=auth_headers)
    assert resp.status_code == 200
    assert "Auto-Switch" in resp.text
    assert 'action="/autoswitch/enable"' in resp.text
    assert 'action="/autoswitch/disable"' in resp.text


def test_autoswitch_disable_route_persists_and_redirects(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    resp = client.post("/autoswitch/disable", headers=auth_headers)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"

    from joystick_notify.config import store as config_store

    assert config_store.load().auto_switch_enabled is False


def test_autoswitch_enable_route_persists_and_redirects(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    client.post("/autoswitch/disable", headers=auth_headers)
    resp = client.post("/autoswitch/enable", headers=auth_headers)
    assert resp.status_code == 303

    from joystick_notify.config import store as config_store

    assert config_store.load().auto_switch_enabled is True


def test_autoswitch_routes_require_auth(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    resp = client.post("/autoswitch/disable")
    assert resp.status_code == 401


def test_api_autoswitch_get_reflects_current_config(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    resp = client.get("/api/autoswitch", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "enabled": True}


def test_api_autoswitch_set_persists_change(client):
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})
    auth_headers = _basic(auth_module.ADMIN_USERNAME, "longenough1")

    resp = client.post("/api/autoswitch", headers=auth_headers, json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "enabled": False}

    from joystick_notify.config import store as config_store

    assert config_store.load().auto_switch_enabled is False


def test_api_autoswitch_not_in_bearer_token_scope(isolated_config):
    # The API token is deliberately scoped to just mode/couch and
    # mode/desk (see _API_TOKEN_PATHS) -- it must not also unlock the
    # auto-switch toggle.
    app = create_app()
    client = TestClient(app, follow_redirects=False)
    client.post("/setup-password", data={"password": "longenough1", "confirm": "longenough1"})

    token, api_token = auth_module.generate_api_token()
    auth_module.save_api_token(api_token)

    resp = client.post("/api/autoswitch", headers={"Authorization": f"Bearer {token}"}, json={"enabled": False})
    assert resp.status_code == 401
