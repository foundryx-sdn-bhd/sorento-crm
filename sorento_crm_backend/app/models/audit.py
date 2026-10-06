"""Audit log model for system-wide change tracking."""
from sqlalchemy import Column, String, DateTime, Text, Index, event, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.database import Base
import uuid


# Keys that never enter audit_logs, whichever model or caller produced the payload
# (issue #1281). `users.password` (a bcrypt hash) and `project_quotation_issues.sign_token`
# (a bearer link token) were both written verbatim before it. log_audit drops them at
# any depth; a new secret column on an audited model belongs here.
AUDIT_SECRET_KEYS = frozenset({
    "password",
    "password_hash",
    "hashed_password",
    "sign_token",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "key_hash",
    "secret",
    "client_secret",
})


def audit_columns_excluding_secrets(table) -> list[str]:
    """Every column of ``table`` except the deny-listed ones, for ``__audit_columns__``."""
    return [c.key for c in table.columns if c.key not in AUDIT_SECRET_KEYS]


class AuditLog(Base):
    """Records INSERT/UPDATE/DELETE on audited entities with old/new values and actor."""
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    entity_type = Column(String(100), nullable=False, index=True)
    # Polymorphic: holds whatever the audited entity's PK is, which is NOT always a
    # UUID (e.g. MarketSegment's PK is a `code` like 'MSEG-A'). Must stay text - 
    # production currently types this column `uuid`, which silently rejects those
    # rows, so market-segment changes have never been audited. See migration 297.
    entity_id = Column(String(100), nullable=False, index=True)
    # INSERT | UPDATE | DELETE, or a named event (e.g. SUPPLIER_COST_LIST_EDIT,
    # COST_VERIFICATION_SETTING - #1288 - the longest named events need 40, not 20).
    action = Column(String(40), nullable=False)
    user_id = Column(UUID(as_uuid=False), nullable=True)  # system, or user id when available
    # Acting contact (respond_contacts.id) for portal/public-link writes where there is
    # no staff user_id. NULL for staff writes and the `system` automation principal.
    # Display resolves this to the contact name BEFORE the user_id -> staff / "System" fallback.
    contact_id = Column(String(100), nullable=True, index=True)
    # clock_timestamp(), not now(): now() is the TRANSACTION's start, so every row one request
    # wrote shared a timestamp and a record's history came back in random order (#1281 S0).
    changed_at = Column(DateTime(timezone=False), server_default=text("clock_timestamp()"), nullable=False)
    old_values = Column(JSONB, nullable=True)
    new_values = Column(JSONB, nullable=True)
    description = Column(Text, nullable=True)
    ip_address = Column(String(100), nullable=True)
    # Per-request correlation id (Sub-plan D Tier-2): all rows written during one
    # HTTP request share this, so a multi-row change is traceable as one action.
    trace_id = Column(String(64), nullable=True, index=True)
    # Multi-company: copied from the CHANGED entity's own company_id by the flush
    # listener (see audit_service._session_before_flush). DELIBERATELY NOT a
    # CompanyScopedMixin - it is written by the global audit listener and must not
    # be auto-stamped/auto-filtered. NULL => the audited entity has no company_id
    # (or a historical row from before this column existed). Filtered ONLY by the
    # admin audit listing via ``admin_listing_company_filter``.
    company_id = Column(UUID(as_uuid=False), nullable=True, index=True)
    # Audit actor (identity S0, #1280; plan section 8). `user_id` is the EFFECTIVE
    # actor; `real_user_id` is who was at the keyboard (differs only when an admin
    # impersonates). `actor_type` is one of user | contact | integration | worker |
    # scheduler | public_link | system; the server default `legacy` is what every
    # pre-S0 row reads, and what a row the old image writes during a swap reads.
    # No Python-side default on purpose: new code always stamps an explicit value.
    actor_type = Column(String(20), nullable=True, server_default="legacy")
    real_user_id = Column(UUID(as_uuid=False), nullable=True)
    auth_method = Column(String(20), nullable=True)
    session_id = Column(UUID(as_uuid=False), nullable=True)
    integration_id = Column(UUID(as_uuid=False), nullable=True)
    job_id = Column(String(128), nullable=True)
    user_agent = Column(String(512), nullable=True)

    # --- Audit standard S0 (#1281, PLAN-audit-standard-26sep.md) ---
    # The record an operator opens to see this change (a child rolls up to its header via
    # the child model's ``__audit_parent__``); a parentless row is its own root.
    root_entity_type = Column(String(100), nullable=True)
    root_entity_id = Column(String(100), nullable=True)
    # Business verb, dotted (``scm.po.confirm``); NULL = plain CRUD.
    event = Column(String(100), nullable=True, index=True)
    # WHO acted is the identity actor above (actor_type, user_id, real_user_id, ...): S0 adds
    # no actor column of its own. ``source`` is the channel, not the actor:
    # ui | portal | chatbot | mcp | n8n | external_api | import | worker | scheduler | script
    # (an operator-run maintenance script, e.g. scripts/merge_customers_by_code.py).
    # Derived server side, never read from a caller header.
    source = Column(String(20), nullable=True)
    reason = Column(Text, nullable=True)
    # One per business action across processes: a job inherits its request's.
    correlation_id = Column(String(64), nullable=True, index=True)

    __table_args__ = (
        Index("ix_audit_logs_entity_type_entity_id", "entity_type", "entity_id"),
        Index("ix_audit_logs_changed_at", "changed_at"),
        Index("ix_audit_logs_user_id", "user_id"),
        Index("ix_audit_logs_real_user_id", "real_user_id"),
        Index("ix_audit_logs_root_entity", "root_entity_type", "root_entity_id"),
    )


# Append-only, enforced by Postgres. Any UPDATE, DELETE or TRUNCATE raises, with one way
# through: the transaction ran ``SET LOCAL sorento.audit_maintenance = 'on'`` AND the current
# role is a member of ``sorento_audit_maintainer`` (a NOLOGIN role; with no such role, only a
# superuser). Anyone can SET a custom setting, so the flag alone is only a statement of intent;
# the role is what the application's own login does not have (review S1 at 7a56073f). The
# retention job and scrub migrations run as a maintainer; a DBA creates the role (a superuser
# only) and grants it to that login, which must not hold CREATEROLE.
# SET LOCAL, inside an explicit transaction: a session-level SET would leave a pooled
# connection in bypass mode.
#
# What this is: a guard against application code, and an application login, editing history.
# What it is NOT, and the bypasses that remain (documented, not closed, in S0):
#   1. the table's OWNER can ``ALTER TABLE audit_logs DISABLE TRIGGER`` or drop the trigger;
#      in a single-role deployment the app login is the owner;
#   2. a superuser can ``SET session_replication_role = replica``, which skips triggers;
#   3. a superuser is a member of every role, so it passes the maintainer check;
#   4. an app login with CREATEROLE could make itself a member (review S1-r2), so the trigger
#      refuses the flag for any CREATEROLE login that is not a superuser, and only a superuser
#      creates the role. Left open on PG15 only (the shipped compose image): a CREATEROLE
#      login may grant any non-superuser role, so it can create a second login without
#      CREATEROLE and grant it the role. PG16 closes that (granting needs ADMIN on the role,
#      which a superuser-created role gives nobody). Run the app login without CREATEROLE.
# Closing 1 needs the table owned by a migration role and ``REVOKE UPDATE, DELETE, TRUNCATE``
# from the app login (an owner decision, PLAN-audit-standard-26sep.md 7.1).
#
# Migration aud_0001_audit_standard_s0 installs this on existing databases; the hook below
# installs it wherever ``create_all`` builds the table (CI's bootstrap_env, the blank test
# schema), so the two cannot drift.
MAINTAINER_ROLE = "sorento_audit_maintainer"
ENSURE_MAINTAINER_ROLE_SQL = """
DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sorento_audit_maintainer') THEN
        -- A superuser only: a CREATEROLE login that creates the role is made a member of it
        -- (PG16's implicit ADMIN grant to the creator), which is the app login in a
        -- single-role deployment (review S1-r2, probe P1).
        IF coalesce((SELECT rolsuper FROM pg_roles WHERE rolname = current_user), false) THEN
            BEGIN
                CREATE ROLE sorento_audit_maintainer NOLOGIN;
            EXCEPTION
                WHEN duplicate_object OR unique_violation THEN
                    NULL;
            END;
        ELSE
            RAISE NOTICE 'sorento_audit_maintainer not created (a superuser must create it): until then only a superuser can maintain audit_logs';
        END IF;
    END IF;
END
$do$
"""
APPEND_ONLY_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION {schema}audit_logs_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    maintainer boolean := false;
BEGIN
    IF coalesce(current_setting('sorento.audit_maintenance', true), '') = 'on' THEN
        -- A CREATEROLE login can make itself a member (PG15: it may grant any non-superuser
        -- role; PG16: it holds ADMIN on a role it created), so its membership proves nothing
        -- (review S1-r2). session_user too: SET ROLE to the maintainer role would hide it.
        IF EXISTS (
            SELECT 1 FROM pg_roles
            WHERE rolname IN (current_user, session_user) AND rolcreaterole AND NOT rolsuper
        ) THEN
            maintainer := false;
        ELSIF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sorento_audit_maintainer') THEN
            maintainer := pg_has_role(current_user, 'sorento_audit_maintainer', 'MEMBER');
        ELSE
            maintainer := coalesce((SELECT rolsuper FROM pg_roles WHERE rolname = current_user), false);
        END IF;
    END IF;
    IF maintainer THEN
        IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
        IF TG_OP = 'UPDATE' THEN RETURN NEW; END IF;
        RETURN NULL;
    END IF;
    RAISE EXCEPTION 'audit_logs is append-only (% refused)', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END
$$
"""
APPEND_ONLY_TRIGGERS_SQL = (
    "CREATE TRIGGER audit_logs_append_only_row BEFORE UPDATE OR DELETE ON {schema}audit_logs "
    "FOR EACH ROW EXECUTE FUNCTION {schema}audit_logs_append_only()",
    "CREATE TRIGGER audit_logs_append_only_truncate BEFORE TRUNCATE ON {schema}audit_logs "
    "FOR EACH STATEMENT EXECUTE FUNCTION {schema}audit_logs_append_only()",
)


@event.listens_for(AuditLog.__table__, "after_create")
def _install_append_only_trigger(target, connection, **kw):  # noqa: ANN001
    translate = connection.get_execution_options().get("schema_translate_map") or {}
    schema = translate.get(target.schema)
    prefix = f'"{schema}".' if schema else ""
    connection.execute(text(ENSURE_MAINTAINER_ROLE_SQL))
    # text(), not exec_driver_sql: the RAISE format's `%` would read as a DBAPI placeholder.
    connection.execute(text(APPEND_ONLY_FUNCTION_SQL.format(schema=prefix)))
    for statement in APPEND_ONLY_TRIGGERS_SQL:
        connection.execute(text(statement.format(schema=prefix)))


class AuditTrailGap(Base):
    """A record whose audit trail was NOT written: the capture failed and the business write
    went ahead (owner ruling 28 Sep 2026 19:1x MYT, PLAN-audit-standard-26sep.md "Best-effort
    capture"). One row per record and action; the matching ``integration_log`` row (channel
    ``audit``) carries the error. A later slice backfills and stamps ``backfilled_at``; the
    system health page counts the open ones.

    A side table, not a column on every audited table: one migration, and a trail's absence is
    readable in one place.
    """

    __tablename__ = "audit_trail_gaps"
    __audit_skip__ = "the audit trail's own failure ledger"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    entity_type = Column(String(100), nullable=False)
    entity_id = Column(String(100), nullable=False)
    action = Column(String(20), nullable=False)
    company_id = Column(UUID(as_uuid=False), nullable=True)
    integration_log_id = Column(UUID(as_uuid=False), nullable=True)
    error = Column(Text, nullable=True)
    occurred_at = Column(DateTime(timezone=False), server_default=text("clock_timestamp()"), nullable=False)
    backfilled_at = Column(DateTime(timezone=False), nullable=True)

    __table_args__ = (
        Index("ix_audit_trail_gaps_entity", "entity_type", "entity_id"),
        Index("ix_audit_trail_gaps_occurred_at", "occurred_at"),
    )
