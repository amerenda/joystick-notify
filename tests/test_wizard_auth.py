import base64
from pathlib import Path

import pytest

from joystick_notify.wizard.auth import (
    check_basic_auth,
    check_bearer_token,
    create_credentials,
    delete_api_token,
    generate_api_token,
    load_api_token,
    load_credentials,
    save_api_token,
    save_credentials,
    validate_bind_address,
    verify_password,
)


def test_verify_password_correct():
    creds = create_credentials("alex", "correct horse battery staple")
    assert verify_password(creds, "correct horse battery staple") is True


def test_verify_password_wrong():
    creds = create_credentials("alex", "correct horse battery staple")
    assert verify_password(creds, "wrong password") is False


def test_round_trip_save_load(tmp_path):
    path = Path(tmp_path) / "credentials.json"
    creds = create_credentials("alex", "hunter2xxxxxxxx")
    save_credentials(creds, path)
    loaded = load_credentials(path)
    assert loaded is not None
    assert loaded.username == "alex"
    assert verify_password(loaded, "hunter2xxxxxxxx") is True


def test_save_credentials_sets_restrictive_permissions(tmp_path):
    path = Path(tmp_path) / "credentials.json"
    save_credentials(create_credentials("alex", "hunter2xxxxxxxx"), path)
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600


def test_load_missing_credentials_returns_none(tmp_path):
    assert load_credentials(Path(tmp_path) / "nope.json") is None


def _basic_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


def test_check_basic_auth_valid():
    creds = create_credentials("alex", "hunter2xxxxxxxx")
    assert check_basic_auth(_basic_header("alex", "hunter2xxxxxxxx"), creds) is True


def test_check_basic_auth_wrong_password():
    creds = create_credentials("alex", "hunter2xxxxxxxx")
    assert check_basic_auth(_basic_header("alex", "wrong"), creds) is False


def test_check_basic_auth_wrong_username():
    creds = create_credentials("alex", "hunter2xxxxxxxx")
    assert check_basic_auth(_basic_header("mallory", "hunter2xxxxxxxx"), creds) is False


def test_check_basic_auth_missing_header():
    creds = create_credentials("alex", "hunter2xxxxxxxx")
    assert check_basic_auth(None, creds) is False


def test_check_basic_auth_malformed_header():
    creds = create_credentials("alex", "hunter2xxxxxxxx")
    assert check_basic_auth("Bearer sometoken", creds) is False
    assert check_basic_auth("Basic not-valid-base64!!!", creds) is False


def test_validate_bind_address_loopback_always_allowed():
    validate_bind_address("127.0.0.1", has_credentials=False)
    validate_bind_address("localhost", has_credentials=False)


def test_validate_bind_address_lan_without_credentials_refused():
    with pytest.raises(ValueError):
        validate_bind_address("0.0.0.0", has_credentials=False)
    with pytest.raises(ValueError):
        validate_bind_address("10.100.20.25", has_credentials=False)


def test_validate_bind_address_lan_with_credentials_allowed():
    validate_bind_address("10.100.20.25", has_credentials=True)


def test_generate_api_token_returns_plaintext_and_stores_only_hash():
    token, api_token = generate_api_token()
    assert len(token) > 20
    assert api_token.token_hash_hex != token
    assert token not in api_token.token_hash_hex


def test_check_bearer_token_valid():
    token, api_token = generate_api_token()
    assert check_bearer_token(f"Bearer {token}", api_token) is True


def test_check_bearer_token_wrong_token():
    _, api_token = generate_api_token()
    assert check_bearer_token("Bearer some-other-token", api_token) is False


def test_check_bearer_token_missing_or_malformed_header():
    _, api_token = generate_api_token()
    assert check_bearer_token(None, api_token) is False
    assert check_bearer_token("Basic dXNlcjpwYXNz", api_token) is False


def test_api_token_round_trip_save_load(tmp_path):
    path = Path(tmp_path) / "api_token.json"
    token, api_token = generate_api_token()
    save_api_token(api_token, path)

    loaded = load_api_token(path)
    assert loaded is not None
    assert check_bearer_token(f"Bearer {token}", loaded) is True


def test_load_missing_api_token_returns_none(tmp_path):
    assert load_api_token(Path(tmp_path) / "nope.json") is None


def test_save_api_token_sets_restrictive_permissions(tmp_path):
    path = Path(tmp_path) / "api_token.json"
    _, api_token = generate_api_token()
    save_api_token(api_token, path)
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600


def test_delete_api_token_removes_file(tmp_path):
    path = Path(tmp_path) / "api_token.json"
    _, api_token = generate_api_token()
    save_api_token(api_token, path)
    assert path.exists()

    delete_api_token(path)
    assert not path.exists()
    assert load_api_token(path) is None


def test_delete_api_token_missing_file_does_not_raise(tmp_path):
    delete_api_token(Path(tmp_path) / "nope.json")
