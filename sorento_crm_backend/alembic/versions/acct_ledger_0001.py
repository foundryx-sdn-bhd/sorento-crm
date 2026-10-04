"""ACCOUNT-LEDGER: `customers.account_level`, seeded once from the name marker.

Revision ID: acct_ledger_0001
Revises: selfref_0001_n8n_sales_view
Create Date: 2026-10-02

"account 1" in a chatbot message selects the ledger whose Account level setting is 1. The
setting is one nullable column on `customers`; null means the row is not a numbered account.

The seed runs ONCE, here, and only where the level is still NULL: `[A/C I]` / `(A/C 2)` in the
name becomes 1 / 2 via `ledger_family.account_level_from_name`. After that the name is never
read again, so an office-edited level is never overwritten (a re-run touches NULL rows only).
Additive: `ADD COLUMN IF NOT EXISTS`, the CHECK is added only when absent.
"""
import sqlalchemy as sa
from alembic import op

revision = "acct_ledger_0001"
down_revision = "selfref_0001_n8n_sales_view"
branch_labels = None
depends_on = None


def seed(connection) -> int:
    """Set `account_level` from the name marker where it is still NULL. Returns rows set."""
    from app.services.ledger_family import account_level_from_name

    rows = connection.execute(
        sa.text("SELECT id, customer_name FROM customers WHERE account_level IS NULL AND customer_name ILIKE '%A/C%'")
    ).fetchall()
    set_count = 0
    for cid, name in rows:
        level = account_level_from_name(name)
        if level is None:
            continue
        connection.execute(
            sa.text("UPDATE customers SET account_level = :n WHERE id = :i AND account_level IS NULL"),
            {"n": level, "i": cid},
        )
        set_count += 1
    return set_count


def upgrade() -> None:
    op.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS account_level SMALLINT")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'ck_customers_account_level'
            ) THEN
                ALTER TABLE customers
                    ADD CONSTRAINT ck_customers_account_level CHECK (account_level >= 1);
            END IF;
        END $$;
        """
    )
    seed(op.get_bind())


def downgrade() -> None:
    op.execute("ALTER TABLE customers DROP CONSTRAINT IF EXISTS ck_customers_account_level")
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS account_level")
