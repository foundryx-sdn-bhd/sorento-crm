"""User management models."""
import enum
from sqlalchemy import CheckConstraint, Column, String, Boolean, Date, DateTime, ForeignKey, Numeric, Text, Index, Integer, UniqueConstraint, text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, ARRAY, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
from app.models.audit import audit_columns_excluding_secrets
import uuid


class UserStatus(str, enum.Enum):
    INACTIVE = "INACTIVE"
    ACTIVE = "ACTIVE"
    BLOCKED = "BLOCKED"


class User(Base):
    __tablename__ = "users"
    __audit_track__ = True  # who changed what (Sub-plan D Tier-2)
    # `__audit_columns__` is set right after the class body: every column minus
    # AUDIT_SECRET_KEYS, derived so a new column is audited without being listed.
    # The bcrypt `password` hash was written into audit_logs on every user write,
    # logins included (#1281).

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    # Nullable since identity S0 (#1280): a phone-only user has no email, and no
    # placeholder is ever written. `ck_users_email_or_phone` keeps at least one of
    # the two. Every write stores `email.strip().lower()`; `uq_users_email_lower`
    # is the case-insensitive backstop beside the original unique index.
    email = Column(String, unique=True, nullable=True, index=True)
    password = Column(String, nullable=True)
    country = Column(String, nullable=True)
    timezone = Column(String, nullable=True)
    name = Column(String, nullable=True)
    # Optional phone number used for user contact.
    contact_number = Column(String, nullable=True)
    # Intentionally String, NOT SQLEnum, even though production types this column
    # as the native `UserStatus` enum. A SQLEnum column returns an enum MEMBER, and
    # call sites compare with `str(user.status) != "ACTIVE"` - in Python 3.12+ that
    # renders as "UserStatus.ACTIVE", so the comparison fails and every login is
    # rejected with "Account not activated" (auth.py). Switching the column type
    # means auditing every `.status` read first.
    status = Column(String, default=UserStatus.INACTIVE.value, nullable=False)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False)
    last_sign_in_at = Column(DateTime(timezone=False), nullable=True)
    # claude.ai connector: consents older than this are refused (set by revoke_all_for_user).
    connector_revoked_at = Column(DateTime(timezone=False), nullable=True)
    email_verified_at = Column(DateTime(timezone=False), nullable=True)
    is_trashed = Column(Boolean, default=False, nullable=False)
    avatar = Column(String, nullable=True)
    # Mirrors attachments.storage_provider: 's3' (S3 + CloudFront) or 'r2' (Cloudflare R2 + CDN).
    avatar_storage_provider = Column(String(16), nullable=False, server_default="s3")
    invited_by_user_id = Column(String, nullable=True)
    is_protected = Column(Boolean, default=False, nullable=False)
    # Marks a machine principal that an integration acts as (AC-AC-05b).
    # Deliberately NOT is_protected: that flag already selects notification
    # recipients in automation_service.py, so reusing it would silently enrol
    # every integration into automation email. Interactive login is separately
    # impossible for these rows -- they carry no password.
    is_integration = Column(Boolean, default=False, nullable=False, server_default="false")
    respond_user_id = Column(String, nullable=True)
    respond_synced = Column(String, default="pending", nullable=False)
    superior_id = Column(String, ForeignKey("users.id"), nullable=True)
    # Multi-company: the company the user was last active in. Restored on login so
    # logout->login returns to the same company. Nullable (backfilled to Sorento).
    last_active_company_id = Column(PG_UUID(as_uuid=False), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True)
    tier = Column(Integer, nullable=True)  # Conversation SLA policy tier (1, 2, ...)
    daily_sla_summary_subscribed = Column(Boolean, default=True, nullable=False)  # email summary opt-in
    # Link to the WhatsApp contact this user is reachable on (resolves respond_io_id).
    # Set by the owner (or once by the identity S0 migration); nothing links it at runtime (see respond_link_service).
    respond_contact_id = Column(String, ForeignKey("respond_contacts.id", ondelete="SET NULL"), nullable=True)
    # When the user last proved they hold `contact_number` (phone sign-in code). Naive UTC.
    phone_verified_at = Column(DateTime(timezone=False), nullable=True)
    # Per-channel notification toggles (default off until a contact is linked).
    notify_whatsapp = Column(Boolean, default=False, nullable=False, server_default="false")  # legacy; superseded by the per-event toggles below
    notify_whatsapp_summary = Column(Boolean, default=False, nullable=False, server_default="false")  # daily summary template
    # Per-event × per-channel SLA-notification toggles. A channel fires only when the
    # stage allows the event (form_sla_configs.notify_assignee / notify_on_escalation)
    # AND the user opted into that channel for that event. Email defaults on (preserves
    # prior always-email behaviour); WhatsApp backfilled from notify_whatsapp.
    notify_email_on_assignment = Column(Boolean, default=True, nullable=False, server_default="true")
    notify_email_on_escalation = Column(Boolean, default=True, nullable=False, server_default="true")
    notify_whatsapp_on_assignment = Column(Boolean, default=False, nullable=False, server_default="false")
    notify_whatsapp_on_escalation = Column(Boolean, default=False, nullable=False, server_default="false")
    # Product-discontinued batch notification opt-in (admin-configured per user). A user
    # with EITHER toggle on is a recipient (in-app always fires for recipients; email /
    # whatsapp each gated by its toggle). Both off => not a recipient, gets nothing.
    notify_email_on_product_discontinued = Column(Boolean, default=False, nullable=False, server_default="false")
    notify_whatsapp_on_product_discontinued = Column(Boolean, default=False, nullable=False, server_default="false")
    # SLA extend-deadline notification opt-ins (PLAN-sla-extend-deadline). Recipient is
    # the NEXT escalation tier assignee. Email defaults on; WhatsApp off (mirrors the
    # assignment/escalation toggle defaults). In-app always fires.
    notify_email_on_deadline_extended = Column(Boolean, default=True, nullable=False, server_default="true")
    notify_whatsapp_on_deadline_extended = Column(Boolean, default=False, nullable=False, server_default="false")
    # Form handling-lock notification opt-ins (PLAN-form-handling-lock). Recipients are
    # the affected parties (assignee / other eligible members / displaced holder) minus
    # the actor. Email defaults on; WhatsApp off (mirrors the assignment defaults). In-app
    # always fires for non-actor recipients.
    notify_email_on_handling = Column(Boolean, default=True, nullable=False, server_default="true")
    notify_whatsapp_on_handling = Column(Boolean, default=False, nullable=False, server_default="false")
    # Mentioned in an internal note (ticket comment). Recipient is each mentioned
    # user minus the author. Email defaults on; there is no WhatsApp twin for this
    # event. In-app always fires.
    notify_email_on_mention = Column(Boolean, default=True, nullable=False, server_default="true")
    # Which contacts' inbound messages push to this user's phone (PLAN-message-push).
    # One of assigned_and_coverage | assigned_only | all_contacts | off. A column and not
    # a preference table because there is exactly ONE event today; the second event
    # (mentions) is the trigger to migrate it into notification_scope_preferences.
    # The server default is the backfill - every pre-existing user opts in at
    # assigned_and_coverage, which is the behaviour the feature ships with.
    notify_push_message_scope = Column(
        String(24), default="assigned_and_coverage", nullable=False,
        server_default="assigned_and_coverage",
    )

    role_assignments = relationship("UserRoleAssignment", back_populates="user", cascade="all, delete-orphan")
    system_logs = relationship("SystemLog", back_populates="user")
    superior = relationship("User", remote_side=[id], backref="subordinates")
    quick_access = relationship("UserQuickAccess", back_populates="user", order_by="UserQuickAccess.sort_order")
    list_column_configs = relationship(
        "UserListColumnConfig",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("users_invited_by_user_id_idx", "invited_by_user_id"),
        Index("users_status_idx", "status"),
        Index("users_respond_synced_idx", "respond_synced"),
        Index("ix_users_respond_contact_id", "respond_contact_id"),
        # One phone == one user. Postgres allows multiple NULLs, so unlinked users are fine.
        UniqueConstraint("contact_number", name="uq_users_contact_number"),
        # Identity S0 (#1280), mirrored by migration identity_0001_s0_model.
        CheckConstraint(
            "email IS NOT NULL OR contact_number IS NOT NULL",
            name="ck_users_email_or_phone",
        ),
        Index("uq_users_email_lower", func.lower(email), unique=True),
        # One WhatsApp contact == one user (AC-01).
        Index(
            "uq_users_respond_contact_id",
            "respond_contact_id",
            unique=True,
            postgresql_where=text("respond_contact_id IS NOT NULL"),
        ),
    )


User.__audit_columns__ = audit_columns_excluding_secrets(User.__table__)


class UserProductDiscontinuedScope(Base):
    """One (company, brand) slice of the catalogue a user is notified about.

    ``company_id`` NULL = every company (which forces ``brand_id`` NULL);
    ``brand_id`` NULL = every brand in that company. A product matches a scope iff
    ``(scope.company_id IS NULL OR = product.company_id) AND
    (scope.brand_id IS NULL OR = product.brand_id)``, so a product carrying no
    brand is only ever reported to an all-brands scope.

    The channel toggles on ``users`` stay HOW a recipient is notified; these rows
    decide WHAT they hear about. Zero rows = zero notices.

    Deliberately a plain ``Base`` and NOT ``CompanyScopedMixin``: this is a user
    preference, not company-owned business data, and the scheduled task that fans
    the batch out runs under a per-task company scope. An owned table would be
    filtered by that scope and would silently drop recipients mid-run.
    """

    __tablename__ = "user_product_discontinued_scopes"

    id = Column(PG_UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    company_id = Column(
        PG_UUID(as_uuid=False), ForeignKey("companies.id", ondelete="CASCADE"), nullable=True
    )
    brand_id = Column(
        PG_UUID(as_uuid=False), ForeignKey("brands.id", ondelete="CASCADE"), nullable=True
    )
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_user_product_discontinued_scopes_user_id", "user_id"),
        Index("ix_user_product_discontinued_scopes_company_id", "company_id"),
        Index("ix_user_product_discontinued_scopes_brand_id", "brand_id"),
        # Dedupe guard. Expression index over coalesced NULLs rather than
        # NULLS NOT DISTINCT, which needs Postgres 15.
        Index(
            "uq_user_product_discontinued_scopes",
            "user_id",
            text("coalesce(company_id, '00000000-0000-0000-0000-000000000000'::uuid)"),
            text("coalesce(brand_id, '00000000-0000-0000-0000-000000000000'::uuid)"),
            unique=True,
        ),
    )


class UserRole(Base):
    __tablename__ = "user_roles"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    slug = Column(String, unique=True, nullable=False)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text, nullable=True)
    is_trashed = Column(Boolean, default=False, nullable=False)
    created_by_user_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    is_protected = Column(Boolean, default=False, nullable=False)
    is_default = Column(Boolean, default=False, nullable=False)
    
    user_assignments = relationship("UserRoleAssignment", back_populates="role", cascade="all, delete-orphan")
    permissions = relationship(
        "UserRolePermission",
        back_populates="role",
        passive_deletes=True,
    )


class UserRoleAssignment(Base):
    """Pivot: user can have multiple roles. Kept in sync with users.role_id for compatibility."""
    __tablename__ = "user_role_assignments"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role_id = Column(String, ForeignKey("user_roles.id", ondelete="CASCADE"), nullable=False)
    assigned_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="role_assignments")
    role = relationship("UserRole", back_populates="user_assignments")

    __table_args__ = (
        Index("ix_user_role_assignments_user_id", "user_id"),
        Index("ix_user_role_assignments_role_id", "role_id"),
        Index("uq_user_role_assignments_user_id_role_id", "user_id", "role_id", unique=True),
    )


class UserPermission(Base):
    __tablename__ = "user_permissions"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    slug = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    created_by_user_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    
    roles = relationship("UserRolePermission", back_populates="permission")


class UserRolePermission(Base):
    __tablename__ = "user_role_permissions"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    role_id = Column(String, ForeignKey("user_roles.id", ondelete="CASCADE"), nullable=False)
    permission_id = Column(String, ForeignKey("user_permissions.id", ondelete="CASCADE"), nullable=False)
    assigned_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    
    role = relationship("UserRole", back_populates="permissions")
    permission = relationship("UserPermission", back_populates="roles")
    
    __table_args__ = (
        Index("user_role_permissions_role_id_permission_id_key", "role_id", "permission_id", unique=True),
    )


class SystemLog(Base):
    __tablename__ = "system_logs"
    __audit_skip__ = "request log, retired in S3"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    entity_id = Column(String, nullable=True)
    entity_type = Column(String, nullable=True)
    event = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    ip_address = Column(String, nullable=True)
    meta = Column(Text, nullable=True)
    
    user = relationship("User", back_populates="system_logs")
    
    __table_args__ = (
        Index("system_logs_user_id_idx", "user_id"),
    )


def _default_unsupported_domains() -> list[str]:
    """`contracts.DEFAULT_UNSUPPORTED_DOMAINS`, via the chatbot module's doorway (AC-931).

    Imported INSIDE the function, not at module scope: this file is imported by nearly
    everything, and a top-level import would make the chatbot package a hard dependency of
    the model layer. The doorway (`app/modules/chatbot/lane_vocabulary.py`) is what keeps
    AC-002's "core never imports the package" true while still leaving one declaration.
    """
    from app.modules.chatbot.lane_vocabulary import default_unsupported_domains

    return default_unsupported_domains()


def _default_chatbot_memory():
    """`SystemSetting.chatbot_memory`'s Python default, through the module doorway core
    may import (AC-002) - the same shape `lane_vocabulary` declares once."""
    from app.modules.chatbot.lane_vocabulary import default_chatbot_memory

    return default_chatbot_memory()


def _default_tier_order() -> list[str]:
    """`tier_gate.TIER_ORDER`, via the chatbot module's doorway - same reasoning as
    `_default_unsupported_domains` above (chatbot turn re-architecture, AC-1502)."""
    from app.modules.chatbot.lane_vocabulary import default_tier_order

    return default_tier_order()


class SystemSetting(Base):
    __tablename__ = "system_settings"
    # id as String so UPDATE/WHERE work when DB column is TEXT (avoids "operator does not exist: text = uuid")
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, default="My Company", nullable=False)
    logo = Column(String, nullable=True)
    # The photograph an admin puts behind the sign-in card. NULL means "none uploaded",
    # and the frontend then draws its designed default wash - which is a finished screen,
    # not a placeholder, so nothing here needs a seed value or a backfill.
    #
    # Two columns for one file, exactly like `users.avatar` / `users.avatar_storage_provider`
    # above: the stable non-signed CDN URL is what is durable, and the provider records which
    # backend currently holds the bytes so a read can ask THAT one for a fresh signed URL.
    # Storing a signed URL instead would put an expiry into the database.
    signin_background = Column(String, nullable=True)
    signin_background_storage_provider = Column(String(16), nullable=True)
    active = Column(Boolean, default=True, nullable=False)
    address = Column(Text, nullable=True)
    website_url = Column(String, nullable=True)
    support_email = Column(String, nullable=True)
    support_phone = Column(String, nullable=True)
    language = Column(String, default="en", nullable=False)
    timezone = Column(String, default="UTC", nullable=False)
    currency = Column(String, default="MYR", nullable=False)
    currency_format = Column(String, default="RM {value}", nullable=False)
    
    social_facebook = Column(String, nullable=True)
    social_twitter = Column(String, nullable=True)
    social_instagram = Column(String, nullable=True)
    social_linkedin = Column(String, nullable=True)
    social_pinterest = Column(String, nullable=True)
    social_youtube = Column(String, nullable=True)
    # #1349: the email theme (brand colour, logo, button, font, footer) as ONE object,
    # validated by app.services.email_layout.EmailTheme and served by its own endpoint
    # (/api/v1/system/email-theme), so it is deliberately NOT in the settings dict builders.
    email_theme = Column(JSONB, nullable=True)
    
    notify_stock_email = Column(Boolean, default=True, nullable=False)
    notify_stock_web = Column(Boolean, default=True, nullable=False)
    notify_stock_threshold = Column(Integer, default=10, nullable=False)
    notify_stock_role_ids = Column(ARRAY(String), nullable=True)
    notify_new_order_email = Column(Boolean, default=True, nullable=False)
    notify_new_order_web = Column(Boolean, default=True, nullable=False)
    notify_new_order_role_ids = Column(ARRAY(String), nullable=True)
    notify_order_status_update_email = Column(Boolean, default=True, nullable=False)
    notify_order_status_update_web = Column(Boolean, default=True, nullable=False)
    notify_order_status_update_role_ids = Column(ARRAY(String), nullable=True)
    notify_payment_failure_email = Column(Boolean, default=True, nullable=False)
    notify_payment_failure_web = Column(Boolean, default=True, nullable=False)
    notify_payment_failure_role_ids = Column(ARRAY(String), nullable=True)
    notify_system_error_failure_email = Column(Boolean, default=True, nullable=False)
    notify_system_error_web = Column(Boolean, default=True, nullable=False)
    notify_system_error_role_ids = Column(ARRAY(String), nullable=True)

    # Complaint <-> DO auto-fulfilment: which Complaint-team tiers (Access-Agent
    # `complaint`, set `complaint`) receive the replacement-DO-delivered email/in-app.
    # Comma list of tier numbers, e.g. "1,2" (Tier 1 + Tier 2) or "1" (Tier 1 only).
    complaint_do_delivered_notify_tiers = Column(
        String(20), nullable=False, server_default="1,2", default="1,2"
    )

    # Project registration clash bars (AC-C5). Two, not one: surfacing is generous
    # because a missed duplicate is silent, blocking is strict because a false block
    # fired often enough teaches users to ignore the warning. Calibrated on the live
    # title corpus -- see app/services/project_clash_service.py for the measurements.
    # AC-I3: months from launch date to delivery, used to bucket the forecast by year.
    # Seeded at 30 from the client's own worked example. A setting rather than a constant
    # because it is a market observation, and it will change before the code does.
    project_delivery_lag_months = Column(
        Integer, nullable=False, server_default="30", default=30
    )

    project_clash_surface_threshold = Column(
        Numeric(4, 3), nullable=False, server_default="0.550", default=0.550
    )
    project_clash_block_threshold = Column(
        Numeric(4, 3), nullable=False, server_default="0.700", default=0.700
    )

    # SCM front planning: the admin PLAN GRAIN policy (plan 5.1, AC-F01). `product` or
    # `location`, rollout default `product`. It is policy, not a per-run selector - each
    # new reorder run stamps the configured value at creation and keeps it, so changing
    # this affects only runs created afterwards. Separate from the buyer's own
    # Planning mode: Auto / Manual, which it never renames or overrides.
    plan_grain = Column(String(20), nullable=False, server_default="product", default="product")

    # SMTP for notification emails (password not returned in read APIs)
    smtp_host = Column(String(255), nullable=True)
    smtp_port = Column(String(10), nullable=True)
    smtp_secure = Column(Boolean, default=True, nullable=False)
    smtp_username = Column(String(255), nullable=True)
    smtp_password = Column(String(255), nullable=True)
    smtp_from = Column(String(255), nullable=True)  # sender address or "Name <email>"

    # Runtime settings that used to be prod env vars (SETTINGS-IN-DB-SORENTO). Read through
    # `app/services/runtime_settings.py`: a value here wins, NULL falls back to the env var of
    # the same name. Secrets are `encrypt_secret` ciphertext and never leave the API in full.
    chat_topic_labeller_enabled = Column(Boolean, nullable=True)  # NULL = CHAT_TOPIC_LABELLER_ENABLED
    connector_ss_to_product_secret_ciphertext = Column(Text, nullable=True)
    connector_product_to_ss_secret_ciphertext = Column(Text, nullable=True)
    connector_connection_id = Column(String(255), nullable=True)
    connector_ss_callback_url = Column(String(2048), nullable=True)
    analytics_ro_database_url_ciphertext = Column(Text, nullable=True)
    respond_webhook_secret_ciphertext = Column(Text, nullable=True)
    # DO-BACKFILL-SETTING: the DO-HISTORY cutover (NULL = DO_ASSUME_DELIVERED_BEFORE).
    do_assume_delivered_before = Column(Date, nullable=True)

    # Takeover cooldown: seconds between a takeover confirm and the actual reassignment,
    # during which the original assignee can reject and the initiator can cancel.
    # 0 = disabled (takeover commits instantly, pre-feature behavior). See PLAN-takeover-cooldown.
    takeover_cooldown_seconds = Column(Integer, nullable=False, server_default="60", default=60)
    # Global default grace window for form-SLA actions (PLAN-form-sla-undo.md). 0 =
    # every action fires immediately, i.e. today's behaviour; a stage may override it.
    form_sla_grace_seconds = Column(Integer, nullable=False, server_default="0", default=0)
    # PHOTO-SEND-CAP round 2 (owner Q1, 9 Oct 2026): how many days a chatbot file short link
    # (`chatbot_file_links`) stays open. NULL = never expires, which is what the owner chose.
    chatbot_file_link_expiry_days = Column(Integer, nullable=True)

    # The two windows a deferred RECORD action waits out (D7/D16, S6): the product has
    # no confirmation dialogs, so the countdown is the way back and its length is the
    # only thing to tune. Destructive covers `<entity>.delete`; reversible covers a
    # status change and anything else that can simply be set back.
    deferred_delete_seconds = Column(Integer, nullable=False, server_default="10", default=10)
    deferred_action_seconds = Column(Integer, nullable=False, server_default="5", default=5)
    # Cost price from the supplier (#1288, AC-S2-20): off for the first rollout (owner ruling
    # 27 Sep 00:45). On, a staff upload goes through Submit/Decide/Return before Apply; off, the
    # uploader applies directly. A supplier-channel set (Lane B) is Pending regardless.
    cost_price_verification_enabled = Column(
        Boolean, nullable=False, server_default="false", default=False
    )
    # Sample requests (SR-CORE card rules 2 and 3): soft-transition switches, OFF on deploy.
    sample_request_require_project = Column(
        Boolean, nullable=False, server_default="false", default=False
    )
    # How many days an untouched price tag collection waits before the sweep
    # closes it (r9 D10). 0 turns the sweep off; 7 is the shipped default.
    price_tag_auto_collect_days = Column(
        Integer, nullable=False, server_default="7", default=7
    )

    # System-health observability (PLAN-system-health-observability):
    # daily digest + immediate watchdog alerts. Recipients = role ids (like notify_*_role_ids).
    health_digest_enabled = Column(Boolean, default=True, nullable=False, server_default="true")
    health_alerts_enabled = Column(Boolean, default=True, nullable=False, server_default="true")
    health_notify_role_ids = Column(ARRAY(String), nullable=True)  # digest + alert recipients (by role); empty -> admins fallback in code
    health_notify_user_ids = Column(ARRAY(String), nullable=True)  # digest + alert recipients (individual users), unioned with role members
    health_integration_fail_threshold = Column(Integer, nullable=False, server_default="10", default=10)  # per-channel 24h failed spike
    health_audit_volume_floor = Column(Integer, nullable=False, server_default="0", default=0)  # 0 = floor alert disabled
    # Default overdue tolerance for scheduled tasks, as a percentage of each task's own
    # interval. Clamped to [60s, 30min] so it degrades sanely from a 30s task to a daily
    # one. Per-task override lives in scheduled_tasks.metadata->>'grace_percent'.
    health_task_grace_percent = Column(Integer, nullable=False, server_default="25", default=25)
    # WhatsApp round-trip SLA: user presses send -> our reply is accepted by Respond.
    # The clock stops at "sent", not delivered - see chat_latency_service.
    chat_latency_p99_target_seconds = Column(Integer, nullable=False, server_default="10", default=10)
    # A single turn past target x this multiplier alerts on its own, with no minimum
    # sample size: at volume, one stalled turn would never move a windowed percentile.
    chat_latency_ceiling_multiplier = Column(Integer, nullable=False, server_default="3", default=3)
    # An incoming with no reply after this long is the shape a dropped webhook takes - 
    # the turn never completes, so it never enters the latency distribution at all.
    chat_latency_no_reply_minutes = Column(Integer, nullable=False, server_default="5", default=5)
    # Below this many paired turns a window percentile is noise, so fleet-level
    # breach alerting stays quiet.
    chat_latency_min_sample = Column(Integer, nullable=False, server_default="30", default=30)
    # Which computed percentile the watchdog holds to the target. Policy, not
    # implementation - a chattier channel may reasonably choose p95.
    chat_latency_percentile = Column(Integer, nullable=False, server_default="99", default=99)
    # My Downloads retention. Nothing purged this table before chat-history CSV exports
    # made the storage cost real; the purge applies to every download kind.
    downloads_retention_days = Column(Integer, nullable=False, server_default="30", default=30)
    # api_call_log retention, two-stage: payloads are the bulk of the bytes and
    # the shortest-lived value, the metadata row stays useful much longer.
    api_call_log_payload_retention_days = Column(Integer, nullable=False, server_default="30", default=30)
    api_call_log_row_retention_days = Column(Integer, nullable=False, server_default="180", default=180)
    # Per-row import outcome detail (import_job_rows). Counts and the aggregated
    # breakdown live on import_jobs.result and are never pruned - only the
    # drill-down rows age out.
    import_job_rows_retention_days = Column(Integer, nullable=False, server_default="90", default=90)

    # New products / import: default product_supplier (standard lead time + supplier)
    default_product_supplier_id = Column(
        PG_UUID(as_uuid=False),
        ForeignKey("suppliers.id", ondelete="SET NULL"),
        nullable=True,
    )
    default_product_standard_lead_time_days = Column(Integer, nullable=False, server_default="90", default=90)

    # The unit a product gets when the source states none - a product-import row with no
    # UOM cell, and any other create that leaves it out. NULL means "no admin has said", and
    # the code falls back to `EA` (`ProductService.DEFAULT_UOM_CODE`).
    #
    # A setting rather than a constant because the constant was WRONG: an older fallback
    # took whatever unit the database returned first and stamped 11,415 products with `L`.
    # A backfill script would have to guess the same thing this column lets somebody state,
    # and a re-import of the product list then applies it.
    default_uom_id = Column(
        PG_UUID(as_uuid=False),
        ForeignKey("units_of_measure.id", ondelete="SET NULL"),
        nullable=True,
    )

    # PLAN-oi-request-cs-reserve.md section 6c, F1: the reserve dialog's own default
    # Location, owner-configurable ("list all the site pool with this BRW (configurable
    # as default)"). Seeded to the BRW pool by code in `oirs_0002_reserve_round2`; NULL
    # means "no configured default", and the dialog falls back to the row's own site
    # pool (R3) exactly as it did before this column existed.
    oi_reserve_default_pool_warehouse_id = Column(
        PG_UUID(as_uuid=False),
        ForeignKey("warehouses.id", ondelete="SET NULL"),
        nullable=True,
    )

    # n8n integration (optional; attachment URL falls back to N8N_WEBHOOK_URL env if unset)
    n8n_attachment_webhook_url = Column(Text, nullable=True)
    n8n_crm_chat_outbound_webhook_url = Column(Text, nullable=True)
    n8n_stock_inquiry_revise_webhook_url = Column(Text, nullable=True)
    # CRM -> n8n respond-close-convo signal on a ticket resolve (AC-M3). Env fallback.
    n8n_close_convo_webhook_url = Column(Text, nullable=True)

    # Procurement: when set, "Send for approval" can skip the approver dialog and email the default user.
    purchase_request_default_approver_user_id = Column(
        String,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    sponsorship_form_default_approver_user_id = Column(
        String,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Email guardrail thresholds: global + per-recipient caps + drainer cadence.
    # Tune below the SMTP provider's documented limits to prevent mail-server flagging.
    email_global_rate_per_window = Column(Integer, nullable=False, server_default="60", default=60)
    email_global_window_seconds = Column(Integer, nullable=False, server_default="60", default=60)
    email_per_recipient_rate_per_window = Column(Integer, nullable=False, server_default="10", default=10)
    email_per_recipient_window_seconds = Column(Integer, nullable=False, server_default="3600", default=3600)
    email_outbox_drain_batch_size = Column(Integer, nullable=False, server_default="20", default=20)
    email_outbox_drain_interval_seconds = Column(Integer, nullable=False, server_default="5", default=5)

    # AI assistant per-turn trace (M2) retention + payload caps. Swept by the
    # background scheduler: `ok` traces past ttl_days; `error`/`flagged` past
    # error_ttl_days. Payloads truncated at max_payload_bytes each.
    ai_trace_ttl_days = Column(Integer, nullable=False, server_default="30", default=30)
    ai_trace_error_ttl_days = Column(Integer, nullable=False, server_default="90", default=90)
    ai_trace_max_payload_bytes = Column(Integer, nullable=False, server_default="16384", default=16384)
    # M2.5 role split: when true, the agent loop runs an explicit planner node up
    # front and compresses raw tool JSON via the semantic_compressor node before
    # feeding it back. Default off - behavioral change, opt-in per PLAN Q7.
    ai_assistant_role_split_enabled = Column(Boolean, nullable=False, server_default="false", default=False)

    # Form handling-lock (PLAN-form-handling-lock): CSV of the source_entity_types the
    # lock is enabled for (e.g. "complaint,purchase_request"). Empty = off for every form
    # = today's status+permission-only gating. Read via
    # handling_lock_service.is_handling_lock_enabled.
    handling_lock_enabled_types = Column(Text, nullable=True, server_default="")

    # Portal submission revisions (PLAN-portal-submission-revisions). Global defaults;
    # per-type overrides live in portal_revision_configs.
    # `portal_revisions_enabled` is the kill switch: false disables revisions for every
    # type regardless of its config row. `portal_max_revisions` is the fallback cap used
    # when a type's own max_revisions is NULL.
    # Both must ALSO appear in the settings GET dict AND SystemSettingUpdate - the
    # routes build a manual dict and silently drop anything not listed there.
    portal_revisions_enabled = Column(Boolean, nullable=False, server_default="true", default=True)
    portal_max_revisions = Column(Integer, nullable=False, server_default="2", default=2)

    # Chatbot media endpoint (PLAN-chatbot-media-endpoint section 2.4). Every number
    # the endpoint enforces is an operator-editable column, not a constant in code -
    # the captain's requirement, and the reason the settings surface exists at all.
    # Same rule as the two blocks above: each of these must ALSO appear in the
    # settings GET dict AND in SystemSettingUpdate, or it never reaches the frontend.
    media_image_monthly_limit = Column(Integer, nullable=False, server_default="50", default=50)
    media_voice_monthly_limit = Column(Integer, nullable=False, server_default="100", default=100)
    media_voice_max_seconds = Column(Integer, nullable=False, server_default="120", default=120)
    media_burst_limit = Column(Integer, nullable=False, server_default="5", default=5)
    media_burst_window_seconds = Column(Integer, nullable=False, server_default="60", default=60)
    media_warn_threshold_percent = Column(Integer, nullable=False, server_default="80", default=80)
    # NULL falls back to the AIAssistantConfig row, matching _resolve_provider.
    media_image_provider = Column(Text, nullable=True)
    media_image_model = Column(Text, nullable=True)
    # NULL means degradation is IMPOSSIBLE, so the monthly quota becomes a hard
    # refusal (`denied_quota`) instead of an accepted-but-degraded extraction.
    # Deliberately no default: a default would make an explicit NULL unwritable
    # (SQLAlchemy cannot tell "set to None" from "unset" on a defaulted column),
    # and shipping a paid model switched on by default is not a decision this
    # feature gets to make on an operator's behalf.
    media_image_degraded_model = Column(Text, nullable=True)
    media_transcribe_model = Column(Text, nullable=False, server_default="whisper-1", default="whisper-1")
    # Voice's own degraded tier, and it ships NULL and UNSEEDED on purpose. The
    # image tiers were measured (PLAN section 14.1) so migration 358 seeds them;
    # no cheaper transcription model has been measured, so none is claimed here.
    # A NULL degraded model means the monthly voice quota is a hard refusal -
    # which is honest, where degrading to the same model and then telling the
    # contact their accuracy has dropped would not be.
    media_voice_degraded_model = Column(Text, nullable=True)
    # pinned | hints | auto. `pinned`/`en` reproduces today's behaviour exactly.
    media_language_mode = Column(String(16), nullable=False, server_default="pinned", default="pinned")
    media_language_pinned = Column(String(16), nullable=False, server_default="en", default="en")
    media_language_hints = Column(Text, nullable=False, server_default="en,ms,zh", default="en,ms,zh")
    # How long the endpoint awaits the worker before returning `pending`. This is
    # the value that bounds the dispatcher's lock. Range 5-90, enforced in the
    # backend validator, not only in the settings form.
    media_sync_wait_seconds = Column(Integer, nullable=False, server_default="30", default=30)
    # The worker's own hard ceiling. Range 5-110, and must be >= the sync wait so a
    # job that outlives the wait still finishes and stays retrievable rather than
    # being killed mid-flight. 110 keeps a maximally misconfigured pair inside the
    # dispatcher's 120 second lock TTL.
    media_extraction_timeout_seconds = Column(Integer, nullable=False, server_default="45", default=45)
    media_max_entities = Column(Integer, nullable=False, server_default="10", default=10)
    # How long the low stock report route holds a chat turn open, waiting for the fresh
    # plan and its workbook, before it answers `pending` and leaves delivery to the worker
    # push (PLAN-low-stock-report S5, AC-43). The owner's ruling on the lavish page was a
    # System Setting rather than a constant only a deploy can move. Range 5-90, enforced in
    # the backend validator the way the media wait is; 40 sits under the chatbot's own 45 s
    # queue-wait budget.
    low_stock_sync_wait_seconds = Column(Integer, nullable=False, server_default="40", default=40)
    # The chatbot report engine's cap (`report-ask`, lane REPORT-RATE-LIMIT-CONFIG): at most
    # this many reports per contact in the window below, read per request so a change on the
    # Chatbot media settings page applies on the next report. 0 means no limit.
    report_ask_rate_limit = Column(Integer, nullable=False, server_default="10", default=10)
    report_ask_rate_window_minutes = Column(Integer, nullable=False, server_default="10", default=10)
    # R1 (H1): the corrected `check_stock` vocabulary makes two lanes reachable that
    # have been dead by typo since they were written (0/150 live fixtures). Turning them
    # on is therefore a DATA change with a test, not a surprise on deploy. Default off.
    chatbot_stock_denial_enabled = Column(Boolean, nullable=False, server_default="false", default=False)
    # PLAN-local-buy-routing-toggle.md (18 Sep 2026): local-supplier Buy routing is a
    # switch, not a hard rule. Default off, since off is the shipped state the owner ruled.
    local_buy_routing_enabled = Column(Boolean, nullable=False, server_default="false", default=False)
    # PLAN-borrow-location-only-8oct.md D1 (8 Oct 2026): "Borrow from another order" is a
    # switch. Off (the default) borrows a location's stock and orders it back on the
    # borrowing line; on is the donor-named borrow the board offered before.
    borrow_from_order_enabled = Column(Boolean, nullable=False, server_default="false", default=False)
    # AC-304 (D5): the ONE list the owner has actually changed, so it is a column and not
    # a table. `not_supported` is decided against this instead of the two literals the JS
    # carries. A6 (chatbot-growth-r1, AC-911, migration 488) removed `spo_allocation` from
    # the shipped default - `crm_procurement_spo_allocations_last_receipt_list` answers it now.
    #
    # AC-931: the Python `default` reads `contracts.DOMAIN_SPEC` through the chatbot
    # module's doorway (core may not import the package - AC-002), so it cannot drift.
    # `server_default` stays a DDL STRING because that is what it is - the literal Postgres
    # writes into the column definition, which must equal what migration 488's
    # `alter_column` wrote and cannot be computed from a Python value at DDL time. It is
    # pinned to the derived list by `tests/chatbot/test_domain_spec.py`, which is the same
    # trade `CHATBOT_READ_ONLY_TOOLS` makes against the MCP catalogue.
    chatbot_unsupported_domains = Column(
        JSONB,
        nullable=False,
        server_default='["goods_receive"]',
        default=lambda: _default_unsupported_domains(),
    )
    # A7 (chatbot-growth-r1, migration 489): the cross-domain probe ladder, per origin
    # domain. `answer.py::run_crossdomain`'s hard inventory<->incoming pair became the
    # first rung of this; `purchase_order` is the second rung on `inventory` (Foundre's
    # rule: "no stock, no incoming, but a PO is placed"). A tenant with
    # `{"inventory": ["incoming"]}` never probes PO at all. D7 (owner ruling, 8 Sep 2026,
    # migration 491): the ladder is stock -> incoming -> PO whichever domain the customer
    # entered from, so `incoming` climbs to `purchase_order` too.
    chatbot_crossdomain_ladder = Column(
        JSONB,
        nullable=False,
        server_default='{"inventory": ["incoming", "purchase_order"], "incoming": ["inventory", "purchase_order"]}',
        default=lambda: {"inventory": ["incoming", "purchase_order"], "incoming": ["inventory", "purchase_order"]},
    )
    # Chatbot turn re-architecture (AC-1502, captain ruling 16 Sep 2026): the tier
    # order, ONE copy - `lanes/business/tier_gate.TIER_ORDER`'s own literal order today,
    # retired from that module and its two other copies once this table is the only
    # source (AC-1594). Not a `chatbot_entity_kinds` row: "tier" is not one of the 12
    # `ENTITY_HINTS` and never gets one.
    chatbot_tier_order = Column(
        JSONB,
        nullable=False,
        server_default='["dealer", "office", "end_user"]',
        default=lambda: _default_tier_order(),
    )
    # Chatbot memory lane A (contract section 2/5): the Memory card's system switch and
    # default context level, in ONE JSONB. `enabled` gates whether a contact with no
    # level of its own gets any memory at all (default OFF, Q1 ruling); `default_level`
    # is the level such a contact gets once switched on (never "off", never null - the
    # settings select has no clear). Replaces the four dead keys the chatbot turn
    # re-architecture shipped here (recall_default/episode_retention_days/profile_fields/
    # focus_reset_events) - none of them were ever read.
    chatbot_memory = Column(
        JSONB,
        nullable=False,
        server_default='{"enabled": false, "default_level": "full"}',
        default=lambda: _default_chatbot_memory(),
    )
    # Which lanes the CRM is allowed to FINISH, by `branch_kind`, one at a time.
    #
    # `contracts.CRM_COMPLETED_BRANCH_KINDS` says what the code CAN complete; this says
    # what it MAY. A lane has to be in both, so shipping a lane and switching it on are
    # two separate events: the code deploys inert, the owner compares it against n8n's
    # answer for as long as they like, and then flips one string. Default `[]` means the
    # CRM completes nothing and every turn delegates exactly as it does today, which is
    # what makes a lane deploy safe on its own.
    #
    # A JSON array rather than a column per lane: there are thirteen branch kinds and this
    # is one decision repeated, not thirteen different ones. An unknown string in the list
    # is ignored with a warning rather than raising, because this is operator data and a
    # typo must not take the turn engine down.
    chatbot_completed_lanes = Column(JSONB, nullable=False, server_default="[]", default=list)
    # Price tag packages (PLAN-price-tag-combos D2): the product class labels whose
    # request lines are warned about when they reach marketing without their catalogue
    # package. NOT NULL with the owner's two classes as the default rather than NULL -
    # a NULL would make the guard warn about nothing on every existing tenant, which
    # reads as the feature not working rather than as a missing default.
    #
    # A JSON array rather than a table: this is one operator preference, a handful of
    # strings long, and the second one can pay for the generalisation.
    price_tag_guarded_classes = Column(
        JSONB,
        nullable=False,
        server_default='["Bathroom Furniture", "Kitchen Sink"]',
        default=lambda: ["Bathroom Furniture", "Kitchen Sink"],
    )
    # AC-810: the two switches that used to be `.env` flags. They are here rather than in
    # `app/config.py` because the owner turns them on and off while watching live turns,
    # and an environment variable makes that a deploy. Read per turn by the engine.
    #
    # `chatbot_business_lane_enabled` says the ported business lane (resolve + gate, S6a)
    # may RUN; `chatbot_completed_lanes` says its arms may ANSWER. Both are required, and
    # they stay independent so deploy, compare, switch on and cut n8n remain four
    # separately reversible steps.
    chatbot_business_lane_enabled = Column(
        Boolean, nullable=False, server_default="false", default=False
    )
    # S7 mode: the CRM orders turns per contact AND owns the tail, so `/chat/turn` returns
    # the finished reply and `/turn/{id}/complete` answers 410 Gone. Its precondition is
    # that every lane in `chatbot_completed_lanes` is one this build can complete, which is
    # why the settings screen confirms before turning it on.
    chatbot_ordering_enabled = Column(
        Boolean, nullable=False, server_default="false", default=False
    )


class UserQuickAccess(Base):
    """Per-user quick access (pinned menu items and attachment folders) for sidebar."""
    __tablename__ = "user_quick_access"
    __audit_skip__ = "per-user UI preference"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    path = Column(String(500), nullable=False)
    label = Column(String(255), nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)

    user = relationship("User", back_populates="quick_access")

    __table_args__ = (
        Index("ix_user_quick_access_user_id", "user_id"),
        Index("ix_user_quick_access_user_id_path", "user_id", "path", unique=True),
    )


class UserListColumnConfig(Base):
    """Per-user per-listing column preferences (visibility + ordering)."""

    __tablename__ = "user_list_column_configs"
    __audit_skip__ = "per-user UI preference"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    # Stable key identifying the listing.
    # In this implementation it is expected to be the RBAC view permission slug.
    listing_key = Column(String(255), nullable=False)

    # JSON payload:
    # - version: int
    # - columnOrder: string[]
    # - columnVisibility: Record<string, boolean>
    config = Column(JSONB, nullable=False)

    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False)

    user = relationship("User", back_populates="list_column_configs")

    __table_args__ = (
        Index("ix_user_list_column_configs_user_id", "user_id"),
        UniqueConstraint("user_id", "listing_key", name="uq_user_list_column_configs_user_id_listing_key"),
    )
