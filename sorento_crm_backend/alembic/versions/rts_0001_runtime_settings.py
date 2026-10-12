"""Runtime settings move from env to `system_settings` (SETTINGS-IN-DB-SORENTO).

Seven nullable columns (NULL = fall back to the env var of the same name, so an install that
sets the env keeps working unchanged) and the `user_management.settings.secrets` slug, granted
to no role: admin and superadmin pass every slug by bypass
(`UserPermissionService.check_user_has_permission`), so the slug is admin-only.

Idempotent: `create_all` may already have added the columns on a bootstrapped database.

Revision ID: rts_0001_runtime_settings
Revises: chat_topics_0003_uuid_id
"""
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "rts_0001_runtime_settings"
down_revision = "chat_topics_0003_uuid_id"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("chat_topic_labeller_enabled", "BOOLEAN"),
    ("connector_ss_to_product_secret_ciphertext", "TEXT"),
    ("connector_product_to_ss_secret_ciphertext", "TEXT"),
    ("connector_connection_id", "VARCHAR(255)"),
    ("connector_ss_callback_url", "VARCHAR(2048)"),
    ("analytics_ro_database_url_ciphertext", "TEXT"),
    ("respond_webhook_secret_ciphertext", "TEXT"),
)

_SLUG = "user_management.settings.secrets"


def upgrade() -> None:
    for name, ddl in _COLUMNS:
        op.execute(f"ALTER TABLE system_settings ADD COLUMN IF NOT EXISTS {name} {ddl}")
    op.get_bind().execute(
        sa.text(
            "INSERT INTO user_permissions (id, slug, name, description, created_at) "
            "VALUES (:id, :slug, :name, :desc, :now) ON CONFLICT (slug) DO NOTHING"
        ),
        {
            "id": str(uuid.uuid4()),
            "slug": _SLUG,
            "name": "Manage integration secrets",
            "desc": "View and change the integration secrets and switches on Settings > Integrations.",
            "now": datetime.now(timezone.utc).replace(tzinfo=None),
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM user_role_permissions WHERE permission_id IN "
            "(SELECT id FROM user_permissions WHERE slug = :s)"
        ),
        {"s": _SLUG},
    )
    bind.execute(sa.text("DELETE FROM user_permissions WHERE slug = :s"), {"s": _SLUG})
    for name, _ddl in reversed(_COLUMNS):
        op.execute(f"ALTER TABLE system_settings DROP COLUMN IF EXISTS {name}")
