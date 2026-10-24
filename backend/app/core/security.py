"""Password, MFA, session, and CSRF primitives."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from functools import lru_cache

import pyotp
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

_PASSWORDS = PasswordHasher(type=Type.ID)
_DUMMY_HASH = _PASSWORDS.hash("constant dummy password for timing parity")


def hash_password(password: str) -> str:
    return _PASSWORDS.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _PASSWORDS.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def verify_dummy_password(password: str) -> None:
    verify_password(_DUMMY_HASH, password)


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = get_settings().encryption_key
    if not key:
        raise RuntimeError("ENCRYPTION_KEY is required for MFA")
    return Fernet(key.encode())


def encrypt_mfa_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode("ascii")


def decrypt_mfa_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode("ascii")
    except InvalidToken as exc:
        raise RuntimeError("Stored MFA secret could not be decrypted") from exc


def new_totp_secret() -> str:
    return pyotp.random_base32()


def verify_totp(secret: str, code: str) -> bool:
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def hash_csrf_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def csrf_matches(cookie_token: str, header_token: str) -> bool:
    return hmac.compare_digest(cookie_token, header_token)


def recovery_code_hash(code: str) -> bytes:
    return hashlib.sha256(code.replace("-", "").upper().encode("ascii")).digest()


def new_recovery_codes(count: int = 10) -> list[str]:
    return [secrets.token_hex(16).upper() for _ in range(count)]


def rate_limit_key(secret_key: str, slug: str, email: str, ip: str) -> bytes:
    message = f"{slug}\0{email}\0{ip}".encode()
    return hmac.digest(secret_key.encode("utf-8"), message, "sha256")
