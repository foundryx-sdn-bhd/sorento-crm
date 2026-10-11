"""CODE-VARIANTS: products.family_code + family_code_manual, backfilled by the one rule.

Plan: documentation/plans/chatbot/PLAN-code-variants.md (AC-2).

Idempotent (IF NOT EXISTS) so a database that converged through `create_all` upgrades
cleanly. The backfill is `app.services.product_family.FAMILY_CODE_SQL`, the SQL twin of
`family_code_for`, and skips rows a person set by hand.

Revision ID: family_code_0001
Revises: chat_backfill_0001
"""
from alembic import op

from app.services.product_family import FAMILY_CODE_SQL

# Kept <=32 chars: alembic_version.version_num is varchar(32).
revision = "family_code_0001"
down_revision = "chat_backfill_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS family_code VARCHAR(100)")
    op.execute(
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS family_code_manual "
        "BOOLEAN NOT NULL DEFAULT false"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_products_company_family_code "
        "ON products (company_id, family_code)"
    )
    op.execute(
        f"UPDATE products SET family_code = {FAMILY_CODE_SQL} WHERE family_code_manual = false"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_products_company_family_code")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS family_code_manual")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS family_code")
