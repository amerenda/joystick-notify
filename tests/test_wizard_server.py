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
