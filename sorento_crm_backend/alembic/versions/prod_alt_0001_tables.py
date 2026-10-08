"""Product alternatives: the `product_alternatives` link table and
`products.alternatives_manual` (PRODUCT-ALTERNATIVES).

Additive only. A link says "use `alternative_product_id` instead of `product_id`", same
company; `source` is `auto_new` (the -NEW code rule) or `manual` (a person on the product
page). `alternatives_manual` is the `variant_link_manual` precedent: once a person edits a
product's alternatives, the rule never touches that product again until Reset to auto.
Idempotent (`IF NOT EXISTS`) so the shared dev DB, which converges through `create_all`, can
take it in any order. Card: documentation/plans/chatbot/CARD-product-alternatives-08oct.md.

Revision ID: prod_alt_0001_tables
Revises: report_engine_0001_prompt
"""
import sqlalchemy as sa
from alembic import op

revision = "prod_alt_0001_tables"
down_revision = "report_engine_0001_prompt"
branch_labels = None
depends_on = None


UPGRADE_SQL = (
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS alternatives_manual BOOLEAN NOT NULL "
    "DEFAULT false",
    """
    CREATE TABLE IF NOT EXISTS product_alternatives (
        id UUID PRIMARY KEY,
        company_id UUID NOT NULL REFERENCES companies(id),
        product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
        alternative_product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
        source VARCHAR(20) NOT NULL DEFAULT 'manual',
        created_at TIMESTAMP NOT NULL DEFAULT now(),
        created_by UUID,
        CONSTRAINT ck_product_alternatives_not_self CHECK (product_id <> alternative_product_id),
        CONSTRAINT ck_product_alternatives_source CHECK (source IN ('auto_new', 'manual'))
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_product_alternatives_pair "
    "ON product_alternatives (product_id, alternative_product_id)",
    "CREATE INDEX IF NOT EXISTS ix_product_alternatives_alternative "
    "ON product_alternatives (alternative_product_id)",
    "CREATE INDEX IF NOT EXISTS ix_product_alternatives_company_id "
    "ON product_alternatives (company_id)",
)


def upgrade() -> None:
    bind = op.get_bind()
    for statement in UPGRADE_SQL:
        bind.execute(sa.text(statement))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS product_alternatives")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS alternatives_manual")
