"""First-run password setup, scrypt hash storage, and HTTP Basic Auth —
implements the "Wizard network exposure and auth" decision in
plans/joystick-notify-v2.md: loopback-only by default, forced password
setup before anything else is reachable, and a hard refusal to bind
non-loopback without a password already configured.

Deliberately stdlib-only (hashlib.scrypt) — no new dependency for
something this project only needs once, at first run.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from ..config.store import default_config_dir

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SALT_BYTES = 16
LOOPBACK_ADDRESSES = {"127.0.0.1", "::1", "localhost"}

# Fixed, predictable login name for the wizard's single admin account.
# Deliberately NOT tied to the OS username: confirmed via live testing
# 2026-08-21 that a user reasonably assumed "admin" (the page never said
# otherwise) and couldn't log in because it was silently the OS login name
# instead. A fixed, well-known name removes the guesswork entirely — this
# has nothing to do with real Linux user accounts, it's just a label on a
# stored password hash.
ADMIN_USERNAME = "admin"


def default_credentials_path() -> Path:
    return default_config_dir() / "credentials.json"


@dataclass
class Credentials:
    username: str
    salt_hex: str
    hash_hex: str


def _derive(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN)


def create_credentials(username: str, password: str) -> Credentials:
    salt = secrets.token_bytes(SALT_BYTES)
    digest = _derive(password, salt)
    return Credentials(username=username, salt_hex=salt.hex(), hash_hex=digest.hex())


def verify_password(creds: Credentials, password: str) -> bool:
    salt = bytes.fromhex(creds.salt_hex)
    expected = bytes.fromhex(creds.hash_hex)
    candidate = _derive(password, salt)
    return secrets.compare_digest(candidate, expected)


def load_credentials(path: Path | None = None) -> Credentials | None:
    path = path or default_credentials_path()
    try:
        with open(path) as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    try:
        return Credentials(**raw)
    except TypeError:
        return None


def save_credentials(creds: Credentials, path: Path | None = None) -> None:
    path = path or default_credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".cred-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(asdict(creds), f)
        os.replace(tmp_path, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def check_basic_auth(header_value: str | None, creds: Credentials) -> bool:
    if not header_value or not header_value.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header_value[len("Basic ") :]).decode("utf-8")
        username, _, password = decoded.partition(":")
    except (ValueError, UnicodeDecodeError):
        return False
    if not secrets.compare_digest(username, creds.username):
        return False
    return verify_password(creds, password)


def default_api_token_path() -> Path:
    return default_config_dir() / "api_token.json"


@dataclass
class ApiToken:
    token_hash_hex: str
    created_at: float


def _hash_token(token: str) -> str:
    # Plain SHA-256, not scrypt: unlike a human-chosen password, this is a
    # 256-bit random token (secrets.token_urlsafe) with no brute-forceable
    # keyspace to slow down -- a fast hash is the right tool here, same
    # reasoning as hashing API keys/webhook secrets anywhere else.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_api_token() -> tuple[str, ApiToken]:
    """Returns (plaintext_token, ApiToken) -- the plaintext is returned
    exactly once, by design: only its hash is ever persisted (see
    save_api_token), matching how the admin password itself is handled.
    """
    token = secrets.token_urlsafe(32)
    return token, ApiToken(token_hash_hex=_hash_token(token), created_at=time.time())


def load_api_token(path: Path | None = None) -> ApiToken | None:
    path = path or default_api_token_path()
    try:
        with open(path) as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    try:
        return ApiToken(**raw)
    except TypeError:
        return None


def save_api_token(token: ApiToken, path: Path | None = None) -> None:
    path = path or default_api_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".apitoken-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(asdict(token), f)
        os.replace(tmp_path, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def delete_api_token(path: Path | None = None) -> None:
    path = path or default_api_token_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def check_bearer_token(header_value: str | None, token: ApiToken) -> bool:
    if not header_value or not header_value.startswith("Bearer "):
        return False
    candidate = header_value[len("Bearer ") :]
    return secrets.compare_digest(_hash_token(candidate), token.token_hash_hex)


def validate_bind_address(bind_address: str, *, has_credentials: bool) -> None:
    """Refuses a non-loopback bind with no password configured — the
    wizard must not silently become LAN-reachable and unauthenticated.
    Raise, don't warn: this is a hard refusal to start, not a log line.
    """
    if bind_address in LOOPBACK_ADDRESSES:
        return
    if not has_credentials:
        raise ValueError(
            f"refusing to bind the wizard to non-loopback address {bind_address!r} with no password "
            "configured. Start with the default loopback bind, set a password via the setup page, "
            "then set wizard.bind_address for LAN access."
        )
