"""Encryption of broker access tokens at rest (Fernet: AES-128-CBC + HMAC-SHA256).

Key: BROKER_TOKEN_ENCRYPTION_KEY (a Fernet key) when set; otherwise, in development/test
only, a key derived from SECRET_KEY with HKDF-SHA256 (Settings refuses an enabled broker
integration without the dedicated key elsewhere). If the `cryptography` package is not
installed, `available()` is false and the connection service refuses to store any token.
"""

from __future__ import annotations

import base64

from app.core.config import Settings

try:  # pragma: no cover - import guard
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
except ImportError:  # pragma: no cover - exercised by monkeypatching `available`
    Fernet = None  # type: ignore[assignment,misc]
    InvalidToken = Exception  # type: ignore[assignment,misc]

PREFIX = "fernet:v1:"


class EncryptionUnavailable(RuntimeError):
    """No encryption library or key: tokens must not be stored."""


class TokenDecryptError(RuntimeError):
    """The stored token cannot be decrypted (key changed or data corrupted)."""


def available() -> bool:
    return Fernet is not None


def _fernet(settings: Settings) -> Fernet:
    if not available():
        raise EncryptionUnavailable("The 'cryptography' package is not installed.")
    if settings.broker_token_encryption_key is not None:
        key = settings.broker_token_encryption_key.get_secret_value().encode()
    else:
        raw = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"quantai-broker-tokens",
                   info=b"broker-token-v1").derive(
            settings.secret_key.get_secret_value().encode())
        key = base64.urlsafe_b64encode(raw)
    try:
        return Fernet(key)
    except ValueError as exc:
        raise EncryptionUnavailable("BROKER_TOKEN_ENCRYPTION_KEY is not a valid Fernet key.") \
            from exc


def encrypt(settings: Settings, token: str) -> str:
    return PREFIX + _fernet(settings).encrypt(token.encode()).decode()


def decrypt(settings: Settings, blob: str) -> str:
    if not blob.startswith(PREFIX):
        raise TokenDecryptError("Unknown token encoding.")
    try:
        return _fernet(settings).decrypt(blob[len(PREFIX):].encode()).decode()
    except InvalidToken as exc:
        raise TokenDecryptError("The stored broker token cannot be decrypted.") from exc
