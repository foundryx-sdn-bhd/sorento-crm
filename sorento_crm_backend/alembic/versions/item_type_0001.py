"""ITEM-TYPE-CRM: `item_types` reference table + `products.item_type_id`.

Revision ID: item_type_0001
Revises: merge_03oct_join5
Create Date: 2026-10-03

AutoCount `Item.ItemType` (MISC, PROJECT, WASTE, KITCHEN SINK, OMEX, ...) kept on products as
reference data, the brand shape: a company-scoped table unique on (company_id, code), back-created
by the products ingest (`product_rules.ensure_reference`), and a nullable SET NULL link on
`products`. Additive only, every statement idempotent.
"""
from alembic import op

revision = "item_type_0001"
down_revision = "merge_03oct_join5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS item_types (
            id UUID PRIMARY KEY,
            company_id UUID NOT NULL REFERENCES companies(id),
            item_type_code VARCHAR(50) NOT NULL,
            item_type_name VARCHAR(150) NOT NULL,
            description TEXT,
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITHOUT TIME ZONE
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_item_types_company_id ON item_types (company_id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_item_types_company_item_type_code "
        "ON item_types (company_id, item_type_code)"
    )
    op.execute(
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS item_type_id UUID "
        "REFERENCES item_types(id) ON DELETE SET NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_products_item_type_id ON products (item_type_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_products_item_type_id")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS item_type_id")
    op.execute("DROP TABLE IF EXISTS item_types")
