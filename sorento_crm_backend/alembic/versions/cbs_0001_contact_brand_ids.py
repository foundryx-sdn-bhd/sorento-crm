"""Per-contact accessible brands (CONTACT-BRAND-SCOPE).

Adds `respond_contacts.brand_ids uuid[] NULL`. NULL (or empty) means every brand, so
every existing contact stays unscoped. No backfill.

Revision ID: cbs_0001_contact_brand_ids
Revises: dev_login_0001
"""
from __future__ import annotations

from alembic import op

revision = "cbs_0001_contact_brand_ids"
down_revision = "dev_login_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE respond_contacts ADD COLUMN IF NOT EXISTS brand_ids uuid[] NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE respond_contacts DROP COLUMN IF EXISTS brand_ids")
