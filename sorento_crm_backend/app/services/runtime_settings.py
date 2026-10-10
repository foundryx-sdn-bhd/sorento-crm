"""Runtime settings that used to be prod env vars (SETTINGS-IN-DB-SORENTO).

Plan: documentation/plans/system/PLAN-settings-in-db-sorento.md ("Contract (Phase 2)").

Each setting is a column on the `system_settings` singleton and keeps its env var as a
fallback: a value saved here wins; NULL means the env var of the same name (an attribute of
`app.config.settings`) applies; neither means unset. Readers call ``get`` on every use, so a
value saved on Settings > Integrations takes effect without a restart.

Secrets are stored as `encrypt_secret` ciphertext in ``<name>_ciphertext`` and never leave
``describe`` in full. The encryption key itself stays in env (``app/utils/field_encryption.py``):
if it changes, a saved secret no longer decrypts and reads as unset here (env fallback), and
``describe`` reports it as ``unreadable`` so the screen can ask for it again.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models.user import SystemSetting
from app.utils.field_encryption import decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)

# Screen order.
FIELDS: tuple[str, ...] = (
    "chat_topic_labeller_enabled",
    "connector_ss_to_product_secret",
    "connector_product_to_ss_secret",
    "connector_connection_id",
    "connector_ss_callback_url",
    "analytics_ro_database_url",
    "respond_webhook_secret",
)
SECRET_FIELDS: frozenset[str] = frozenset({
    "connector_ss_to_product_secret",
    "connector_product_to_ss_secret",
    "analytics_ro_database_url",
    "respond_webhook_secret",
})
BOOL_FIELDS: frozenset[str] = frozenset({"chat_topic_labeller_enabled"})

AUDIT_EVENT = "RUNTIME_SETTINGS"


def _column(name: str) -> str:
    return f"{name}_ciphertext" if name in SECRET_FIELDS else name


def _env(name: str) -> Any:
    value = getattr(settings, name, None)
    if name in BOOL_FIELDS:
        return None if value is None else bool(value)
    return value or None


def _stored(row: Optional[SystemSetting], name: str) -> tuple[Any, bool]:
    """(the saved value or None, unreadable). A ciphertext no key opens counts as None."""
    raw = getattr(row, _column(name), None) if row is not None else None
    if raw is None or name not in SECRET_FIELDS:
        return raw, False
    try:
        return decrypt_secret(raw) or None, False
    except ValueError:
        logger.warning("runtime_settings: %s does not decrypt (key changed?); using the env fallback", name)
        return None, True


def _row(db: Session) -> Optional[SystemSetting]:
    return db.query(SystemSetting).first()


def _resolve(name: str, row: Optional[SystemSetting]) -> tuple[Any, str, bool]:
    """(effective value, source, unreadable)."""
    stored, unreadable = _stored(row, name)
    if stored is not None:
        return stored, "db", unreadable
    env = _env(name)
    if env is not None:
        return env, "env", unreadable
    return (False if name in BOOL_FIELDS else None), "unset", unreadable


def get_many(names: tuple[str, ...], db: Optional[Session] = None) -> dict[str, Any]:
    """The effective values of several settings from one read. ``db=None`` opens (and closes)
    its own session. A failed read is logged and answered from env, so a reader never breaks."""
    unknown = set(names) - set(FIELDS)
    if unknown:
        raise KeyError(sorted(unknown))
    own = db is None
    try:
        session = SessionLocal() if own else db
        try:
            row = _row(session)
        finally:
            if own:
                session.close()
    except Exception:  # noqa: BLE001 - fall back to env rather than fail the caller
        logger.exception("runtime_settings: could not read system_settings")
        row = None
    return {name: _resolve(name, row)[0] for name in names}


def get(name: str, db: Optional[Session] = None) -> Any:
    """The effective value of one setting (see ``get_many``)."""
    return get_many((name,), db)[name]


def _mask(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return "****" if len(value) <= 8 else f"****{value[-4:]}"


def describe(db: Session) -> dict[str, dict[str, Any]]:
    """Every setting with its source; secrets masked, never in full."""
    row = _row(db)
    out: dict[str, dict[str, Any]] = {}
    for name in FIELDS:
        value, source, unreadable = _resolve(name, row)
        secret = name in SECRET_FIELDS
        out[name] = {
            "source": source,
            "secret": secret,
            "value": None if secret else value,
            "masked": _mask(value) if secret else None,
            "unreadable": unreadable,
        }
    return out


def _normalise(name: str, value: Any) -> Any:
    if name in BOOL_FIELDS:
        return None if value is None else bool(value)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def update(db: Session, payload: dict[str, Any], actor_user_id: Optional[str]) -> list[str]:
    """Apply the provided fields (omitted = keep; None / "" = clear), commit, and write one
    audit row naming what changed. Secret values never enter the audit row: their names travel
    as list values because the redactor drops keys ending in ``_secret``. Returns the changed
    field names."""
    unknown = set(payload) - set(FIELDS)
    if unknown:
        raise ValueError(f"Unknown runtime settings: {sorted(unknown)}")

    row = _row(db)
    if row is None:
        row = SystemSetting()
        db.add(row)
        db.flush()

    changes: dict[str, dict[str, Any]] = {}
    secrets_set: list[str] = []
    secrets_cleared: list[str] = []
    for name in FIELDS:
        if name not in payload:
            continue
        new = _normalise(name, payload[name])
        column = _column(name)
        if name in SECRET_FIELDS:
            old_raw = getattr(row, column)
            if new is None:
                if old_raw is None:
                    continue
                setattr(row, column, None)
                secrets_cleared.append(name)
            else:
                old, _unreadable = _stored(row, name)
                if old == new:
                    continue
                setattr(row, column, encrypt_secret(new))
                secrets_set.append(name)
        else:
            old = getattr(row, column)
            if old == new:
                continue
            setattr(row, column, new)
            changes[name] = {"old": old, "new": new}

    changed = list(changes) + secrets_set + secrets_cleared
    if changed:
        from app.services.audit_service import log_audit

        log_audit(
            db, "system_settings", str(row.id), "UPDATE",
            event=AUDIT_EVENT,
            new_values={
                "changes": changes,
                "secrets_set": secrets_set,
                "secrets_cleared": secrets_cleared,
            },
            user_id=actor_user_id,
        )
    db.commit()
    return changed
