"""CS routing conditions + No CS finish (FORM-ROUTING-CONDITIONS).

Plan: documentation/plans/forms/PLAN-form-routing-conditions.md.

1. `customers.debtor_type` (project | project_cash | cash_sales, NULL = unset) with a CHECK.
2. Backfill of rows still NULL: `302-` codes -> project_cash; `301-` codes whose name starts
   with the word CASH stay NULL; everything else -> project. Re-stated in SQL here (a
   migration must not import app code that can change later);
   app/services/debtor_type.py holds the same rule and a test pins both together.
3. `purchase_requests.customer_id` (FK customers ON DELETE SET NULL) + index.
4. `respond_contact_cs_routing.cs_pic_user_id` nullable (NULL = No CS).

DDL is idempotent: the shared dev DB converges by create_all.

Revision ID: frc_0001_debtor_type_no_cs
Revises: ac_consign_0001_doc_type
Create Date: 2026-10-14
"""
from alembic import op
from sqlalchemy import text

revision = "frc_0001_debtor_type_no_cs"
down_revision = "ac_consign_0001_doc_type"
branch_labels = None
depends_on = None

_CHECK = "ck_customers_debtor_type"


def backfill_debtor_type(connection) -> int:
    """Fill debtor_type where it is NULL; returns the number of rows changed."""
    result = connection.execute(
        text(
            r"""
            UPDATE customers SET debtor_type = CASE
                WHEN btrim(customer_code) LIKE '302-%' THEN 'project_cash'
                ELSE 'project'
            END
            WHERE debtor_type IS NULL
              AND NOT (btrim(customer_code) LIKE '301-%' AND customer_name ~* '^\s*CASH\M')
            """
        )
    )
    return result.rowcount or 0


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS debtor_type VARCHAR(20)"))
    conn.execute(text(f"ALTER TABLE customers DROP CONSTRAINT IF EXISTS {_CHECK}"))
    conn.execute(
        text(
            f"ALTER TABLE customers ADD CONSTRAINT {_CHECK} CHECK "
            "(debtor_type IS NULL OR debtor_type IN ('project', 'project_cash', 'cash_sales'))"
        )
    )
    backfill_debtor_type(conn)

    conn.execute(
        text(
            "ALTER TABLE purchase_requests ADD COLUMN IF NOT EXISTS customer_id UUID "
            "REFERENCES customers(id) ON DELETE SET NULL"
        )
    )
    conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_purchase_requests_customer_id ON purchase_requests (customer_id)")
    )

    conn.execute(text("ALTER TABLE respond_contact_cs_routing ALTER COLUMN cs_pic_user_id DROP NOT NULL"))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DELETE FROM respond_contact_cs_routing WHERE cs_pic_user_id IS NULL"))
    conn.execute(text("ALTER TABLE respond_contact_cs_routing ALTER COLUMN cs_pic_user_id SET NOT NULL"))
    conn.execute(text("DROP INDEX IF EXISTS ix_purchase_requests_customer_id"))
    conn.execute(text("ALTER TABLE purchase_requests DROP COLUMN IF EXISTS customer_id"))
    conn.execute(text(f"ALTER TABLE customers DROP CONSTRAINT IF EXISTS {_CHECK}"))
    conn.execute(text("ALTER TABLE customers DROP COLUMN IF EXISTS debtor_type"))
