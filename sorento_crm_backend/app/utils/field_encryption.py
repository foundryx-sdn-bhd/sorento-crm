"""Symmetric encryption for stored secrets (Respond workspace keys, integration credentials,
AI assistant provider keys).

Keys: FIELD_ENCRYPTION_KEY, comma-separated Fernet keys with the current one first. The
first encrypts; every listed key decrypts, and so does the key derived from JWT_SECRET,
which is what encrypted everything before FIELD_ENCRYPTION_KEY existed. Once
`scripts/reencrypt_field_secrets.py --apply` has moved every stored token onto the
current key, JWT_SECRET can rotate without losing a stored secret.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.config import settings

logger = logging.getLogger(__name__)

# Fernet token layout: version byte 0x80, 8-byte timestamp, 16-byte IV, AES-CBC
# ciphertext (whole 16-byte blocks, at least one), 32-byte HMAC.
_FERNET_VERSION = 0x80
_FERNET_OVERHEAD = 1 + 8 + 16 + 32


def _field_keys() -> list[Fernet]:
    raw = (getattr(settings, "field_encryption_key", "") or "").split(",")
    keys = []
    for position, key in enumerate((k.strip() for k in raw if k.strip()), start=1):
        try:
            keys.append(Fernet(key.encode("ascii")))
        except (ValueError, binascii.Error, UnicodeEncodeError) as e:
            # The key itself never goes into the message.
            raise ValueError(f"FIELD_ENCRYPTION_KEY entry {position} is not a valid Fernet key") from e
    return keys


def _jwt_derived_key() -> Fernet | None:
    secret = (settings.jwt_secret or "").encode()
    if not secret:
        return None
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret).digest()))


def field_encryption_key_configured() -> bool:
    return bool(_field_keys())


# Where FIELD_ENCRYPTION_KEY may be unset (the JWT-derived key alone is used). Mirrors
# app.services.dev_login.ALLOWED_ENVIRONMENTS plus "ci".
_KEYLESS_ENVIRONMENTS = frozenset({"development", "dev", "local", "test", "ci"})


def assert_field_encryption_ready(environment: str | None) -> None:
    """Boot check for the API (app/main.py) and the worker (worker.py).

    A malformed FIELD_ENCRYPTION_KEY raises here rather than reading every stored secret
    as unset at runtime. Outside a dev environment an unset key refuses to start: new
    secrets would otherwise be encrypted under the JWT_SECRET-derived key, and rotating
    JWT_SECRET would then destroy them.
    """
    if _field_keys():
        return
    if (environment or "").strip().lower() not in _KEYLESS_ENVIRONMENTS:
        raise RuntimeError(
            f"FIELD_ENCRYPTION_KEY is not set and ENVIRONMENT={environment!r} is not a dev "
            f"environment ({sorted(_KEYLESS_ENVIRONMENTS)}). Refusing to start: stored secrets "
            "would be encrypted under the JWT_SECRET-derived key. See "
            "documentation/plans/security/PLAN-ai-key-encrypt-08oct.md."
        )


def _keys() -> list[Fernet]:
    """Current key first, then older FIELD_ENCRYPTION_KEY entries, then the JWT-derived key."""
    keys = _field_keys()
    legacy = _jwt_derived_key()
    if legacy is not None:
        keys.append(legacy)
    if not keys:
        raise ValueError("FIELD_ENCRYPTION_KEY (or JWT_SECRET) is required for field encryption")
    return keys


def _fernet() -> MultiFernet:
    return MultiFernet(_keys())


def opens_with_current_key(ciphertext: str) -> bool:
    """True when the key that encrypts new values also opens ``ciphertext``."""
    try:
        _keys()[0].decrypt(ciphertext.encode("ascii"))
        return True
    except (InvalidToken, UnicodeEncodeError):
        return False


def reencrypt_secret(ciphertext: str) -> str:
    """``ciphertext`` re-encrypted under the current key. Raises ValueError if no key opens it."""
    try:
        return _fernet().rotate(ciphertext.encode("ascii")).decode("ascii")
    except InvalidToken as e:
        raise ValueError("Could not decrypt secret (wrong key or corrupted data)") from e


def encrypt_secret(plain: str) -> str:
    if plain is None:
        plain = ""
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as e:
        raise ValueError("Could not decrypt secret (wrong key or corrupted data)") from e


def is_fernet_token(value: str | None) -> bool:
    """True when ``value`` has the Fernet token FORMAT. Says nothing about whether this
    deployment's key opens it; that is ``decrypt_secret``'s job.

    Used to tell rows written before a column was encrypted from rows written after.
    No provider key (``sk-``, ``sk-ant-``, ``AIza``) decodes to a 0x80 version byte.
    """
    if not value:
        return False
    try:
        raw = base64.urlsafe_b64decode(value.encode("ascii"))
    except (binascii.Error, UnicodeEncodeError, ValueError):
        return False
    body = len(raw) - _FERNET_OVERHEAD
    return body >= 16 and body % 16 == 0 and raw[0] == _FERNET_VERSION


class EncryptedText(TypeDecorator):
    """A Text column holding a Fernet token; the ORM attribute holds the plain value.

    Writes always encrypt. Reads decrypt a Fernet-format value and pass any other
    value through unchanged, so rows written before the column was encrypted keep
    working until a backfill encrypts them. A Fernet-format value no configured key
    opens reads as None, never as the token itself.
    """

    impl = Text
    cache_ok = True

    def __init__(self, label: str = "", *args, **kwargs):
        # Names the column in the decrypt-failure log; never the value.
        self.label = label
        super().__init__(*args, **kwargs)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return encrypt_secret(value)

    def process_result_value(self, value, dialect):
        if value is None or not is_fernet_token(value):
            return value
        try:
            return decrypt_secret(value)
        except ValueError:
            logger.error(
                "Could not decrypt %s; reading it as unset. No FIELD_ENCRYPTION_KEY entry (nor the "
                "JWT_SECRET-derived fallback) opens it.",
                self.label or "an encrypted column",
            )
            return None
