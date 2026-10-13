"""Runtime settings that used to be prod env vars (SETTINGS-IN-DB-SORENTO).

Plan: documentation/plans/system/PLAN-settings-in-db-sorento.md ("Contract (Phase 2)").

Each setting is a column on the `system_settings` singleton and keeps its env var as a
fallback: a value saved here wins; NULL means the env var of the same name (an attribute of
`app.config.settings`) applies; neither means unset. Readers call ``get`` on every use, so a
value saved on Settings > Integrations takes effect without a restart (within
``CACHE_TTL_SECONDS`` on another worker process).

Secrets are stored as `encrypt_secret` ciphertext in ``<name>_ciphertext`` and never leave
``describe`` in full. The encryption key itself stays in env (``app/utils/field_encryption.py``):
if it changes, a saved secret no longer decrypts and reads as unset here, WITHOUT the env
fallback (fail closed), and ``describe`` reports it as ``unreadable`` so the screen can ask for
it again.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
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
        # The Settings default is False, so False cannot say whether the env var was set; it
        # is the same effective value as unset, and reported as unset.
        return True if value else None
    return value or None


# A snapshot of the stored columns, for readers that pass no session. The connector check runs
# for any request carrying `Authorization: Connector ...`, before a signature is verified, so
# without this an unauthenticated flood would cost one database read each (security review H1).
# `update` clears it in this process; another worker sees a change within CACHE_TTL_SECONDS.
CACHE_TTL_SECONDS = 5.0
_READ_FAILED = object()
# (generation, read at, snapshot), swapped whole so a reader never sees half of it. A save bumps
# the generation, and a read that started before the save does not write its older snapshot
# back (security re-check N1, N2).
_cache: tuple[int, float, Any] = (0, float("-inf"), None)
_cache_lock = threading.Lock()


def clear_cache() -> None:
    global _cache
    with _cache_lock:
        _cache = (_cache[0] + 1, float("-inf"), None)


def _snapshot(row: Optional[SystemSetting]) -> dict[str, Any]:
    return {_column(name): getattr(row, _column(name), None) for name in FIELDS} if row else {}


def _stored(snap: Any, name: str) -> tuple[Any, bool]:
    """(the saved value or None, unreadable)."""
    raw = snap.get(_column(name)) if isinstance(snap, dict) else None
    if raw is None or name not in SECRET_FIELDS:
        return raw, False
    try:
        return decrypt_secret(raw) or None, False
    except ValueError:
        logger.warning("runtime_settings: %s does not decrypt (key changed?); treated as unset", name)
        return None, True


def _row(db: Session) -> Optional[SystemSetting]:
    return db.query(SystemSetting).first()


def _resolve(name: str, snap: Any) -> tuple[Any, str, bool]:
    """(effective value, source, unreadable).

    Secrets fail closed (security review M2): a saved secret that no longer decrypts, or a
    failed read, never falls back to env, because the env value may be the one the saved
    secret replaced. Only a secret that was never saved (or was cleared) uses env."""
    secret = name in SECRET_FIELDS
    if snap is _READ_FAILED and secret:
        return None, "unset", False
    stored, unreadable = _stored(snap, name)
    if stored is not None:
        return stored, "db", False
    if unreadable:
        return None, "db", True
    env = _env(name)
    if env is not None:
        return env, "env", False
    return (False if name in BOOL_FIELDS else None), "unset", False


def _read(db: Optional[Session]) -> Any:
    """The stored snapshot, or _READ_FAILED. ``db=None`` uses the short cache, else its own
    session."""
    if db is not None:
        try:
            return _snapshot(_row(db))
        except Exception:  # noqa: BLE001 - see _resolve: secrets fail closed, the rest use env
            logger.exception("runtime_settings: could not read system_settings")
            return _READ_FAILED
    global _cache
    generation, at, cached = _cache
    now = time.monotonic()
    if now - at < CACHE_TTL_SECONDS:
        return cached
    try:
        session = SessionLocal()
        try:
            snap = _snapshot(_row(session))
        finally:
            session.close()
    except Exception:  # noqa: BLE001
        logger.exception("runtime_settings: could not read system_settings")
        snap = _READ_FAILED  # cached too, so an outage is not one attempt per request
    with _cache_lock:
        if _cache[0] == generation:
            _cache = (generation, now, snap)
    return snap


def get_many(names: tuple[str, ...], db: Optional[Session] = None) -> dict[str, Any]:
    """The effective values of several settings from one read (see ``_read``)."""
    unknown = set(names) - set(FIELDS)
    if unknown:
        raise KeyError(sorted(unknown))
    snap = _read(db)
    return {name: _resolve(name, snap)[0] for name in names}


def get(name: str, db: Optional[Session] = None) -> Any:
    """The effective value of one setting (see ``get_many``)."""
    return get_many((name,), db)[name]


def _mask(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return "****" if len(value) <= 8 else f"****{value[-4:]}"


def describe(db: Session) -> dict[str, dict[str, Any]]:
    """Every setting with its source; secrets masked, never in full."""
    snap = _snapshot(_row(db))
    out: dict[str, dict[str, Any]] = {}
    for name in FIELDS:
        value, source, unreadable = _resolve(name, snap)
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

    # This save's audit is the one RUNTIME_SETTINGS row below; the generic listener would add a
    # second, blank row for the same flush (the secret columns are redacted from it), and an
    # INSERT row when the singleton is created here.
    row = _row(db)
    if row is None:
        row = SystemSetting(id=str(uuid.uuid4()))
        db.add(row)
    skip = ("system_settings", str(row.id))
    db.info.setdefault("skip_audit_for", []).append(skip)
    try:

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
                    old, _unreadable = _stored(_snapshot(row), name)
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
    finally:
        skips = db.info.get("skip_audit_for") or []
        if skip in skips:
            skips.remove(skip)
    clear_cache()
    return changed
