"""WC-SEAT-COMPAT: the `product_compatibility` link table (seat cover SKU -> WC SKU).

Additive only. One row = "seat cover `seat_cover_product_id` fits WC `wc_product_id`",
same company; `source` is `flyer` (the owner-reviewed flyer import) or `manual` (a person
on the product page). Idempotent (`IF NOT EXISTS`) so the shared dev DB, which converges
through `create_all`, can take it in any order.
Plan: documentation/plans/products/PLAN-wc-seat-compat-12oct.md.

Revision ID: wc_compat_0001_table
Revises: family_code_0001
"""
import sqlalchemy as sa
from alembic import op

# Kept <=32 chars: alembic_version.version_num is varchar(32).
revision = "wc_compat_0001_table"
down_revision = "family_code_0001"
branch_labels = None
depends_on = None


UPGRADE_SQL = (
    """
    CREATE TABLE IF NOT EXISTS product_compatibility (
        id UUID PRIMARY KEY,
        company_id UUID NOT NULL REFERENCES companies(id),
        seat_cover_product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
        wc_product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
        source VARCHAR(20) NOT NULL DEFAULT 'manual',
        created_at TIMESTAMP NOT NULL DEFAULT now(),
        created_by UUID,
        CONSTRAINT ck_product_compatibility_not_self
            CHECK (seat_cover_product_id <> wc_product_id),
        CONSTRAINT ck_product_compatibility_source CHECK (source IN ('flyer', 'manual'))
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_product_compatibility_pair "
    "ON product_compatibility (seat_cover_product_id, wc_product_id)",
    "CREATE INDEX IF NOT EXISTS ix_product_compatibility_wc "
    "ON product_compatibility (wc_product_id)",
    "CREATE INDEX IF NOT EXISTS ix_product_compatibility_company_id "
    "ON product_compatibility (company_id)",
)


def upgrade() -> None:
    bind = op.get_bind()
    for statement in UPGRADE_SQL:
        bind.execute(sa.text(statement))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS product_compatibility")
