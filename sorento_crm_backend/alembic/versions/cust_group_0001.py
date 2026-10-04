"""CUSTOMER-GROUP: `customer_groups` and `customers.customer_group_id`. DDL only.

Revision ID: cust_group_0001
Revises: picker_no_cap_0001
Create Date: 2026-10-02

A group is one company's customer made of several ledgers, and only the office puts a ledger
in one (owner ruling 3 Oct 2026: no automatic name-matching joins, explicit links only).

This revision used to seed groups on upgrade by joining ledgers whose names looked alike
(`ledger_family_key`). It no longer does: upgrade creates the table and the column and
assigns NO customer to any group. The revision id and chain are unchanged, so a database
that already ran the old body (dev) stays at the same head; its seeded rows are left as they
are. Groups are set by hand (bulk "Set customer group", CUSTOMER-BULK-OPS).
Additive: `CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, the FK only when absent.
"""
from alembic import op

revision = "cust_group_0001"
down_revision = "picker_no_cap_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_groups (
            id UUID PRIMARY KEY,
            company_id UUID NOT NULL REFERENCES companies(id),
            name VARCHAR(255) NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT now(),
            updated_at TIMESTAMP NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_customer_groups_company_id ON customer_groups (company_id)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_customer_groups_company_name_lower "
        "ON customer_groups (company_id, lower(name))"
    )
    op.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS customer_group_id UUID")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'fk_customers_customer_group_id'
            ) THEN
                ALTER TABLE customers
                    ADD CONSTRAINT fk_customers_customer_group_id
                    FOREIGN KEY (customer_group_id) REFERENCES customer_groups(id) ON DELETE SET NULL;
            END IF;
        END $$;
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_customers_customer_group_id ON customers (customer_group_id)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE customers DROP CONSTRAINT IF EXISTS fk_customers_customer_group_id")
    op.execute("DROP INDEX IF EXISTS ix_customers_customer_group_id")
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS customer_group_id")
    op.execute("DROP TABLE IF EXISTS customer_groups")
