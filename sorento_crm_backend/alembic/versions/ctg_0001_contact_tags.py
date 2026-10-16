"""CONTACT-TAGS S1: the tag catalog and the contact-to-tag junction.

Additive only and idempotent (`IF NOT EXISTS`) so a DB that converged through `create_all`
can take it in any order.
Plan: documentation/plans/contacts/PLAN-contact-tags-16oct.md.

Revision ID: ctg_0001_contact_tags
Revises: sds_0001_stock_debt_snapshots
"""
from alembic import op

# Kept <=32 chars: alembic_version.version_num is varchar(32).
revision = "ctg_0001_contact_tags"
down_revision = "sds_0001_stock_debt_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS contact_tags (
            id UUID PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            description TEXT,
            is_active BOOLEAN NOT NULL DEFAULT true,
            sort_order INTEGER NOT NULL DEFAULT 0,
            settings JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMP NOT NULL DEFAULT now(),
            updated_at TIMESTAMP NOT NULL DEFAULT now(),
            created_by TEXT,
            updated_by TEXT
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_contact_tags_lower_name ON contact_tags (lower(name))")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS respond_contact_tags (
            contact_id TEXT NOT NULL REFERENCES respond_contacts(id) ON DELETE CASCADE,
            tag_id UUID NOT NULL REFERENCES contact_tags(id) ON DELETE CASCADE,
            created_at TIMESTAMP NOT NULL DEFAULT now(),
            created_by TEXT,
            PRIMARY KEY (contact_id, tag_id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_respond_contact_tags_tag_id ON respond_contact_tags (tag_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS respond_contact_tags")
    op.execute("DROP TABLE IF EXISTS contact_tags")
