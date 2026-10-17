"""Finance S5: a billing document's outstanding balance, due date and the time it was read.

Card: documentation/plans/finance/CARD-ac-billing-17oct.md section 2 (S5). The owner (15 Oct)
asked for outstanding per invoice, read-only. Three nullable columns on
`finance.billing_documents`, written only by the `ar_outstanding` push (contract 2.10):

- `outstanding Numeric(15,2)`: NULL is "not synced or not posted to GL", never 0.
- `due_date Date`.
- `outstanding_as_of timestamp`: the as-of of the push that last set it (the stale guard).

No backfill: a row stays NULL until its debtor's first push. Idempotent both ways
(`IF NOT EXISTS` / `IF EXISTS`), so a database that `create_all` already converged is not an
error. The customer total needs no column: it goes into `customers.ar_outstanding`.

Revision ID: fin_0003_billing_outstanding
Revises: sr_0001_sample_requests
Create Date: 2026-10-17
"""
from alembic import op

from app.models.sales import translated_schema

revision = "fin_0003_billing_outstanding"
down_revision = "sr_0001_sample_requests"
branch_labels = None
depends_on = None

SCHEMA = "finance"
TABLE = "billing_documents"


def upgrade() -> None:
    # `finance`, or the migration test's scratch copy of it (`fin_0002`).
    schema = translated_schema(op.get_bind(), SCHEMA)
    op.execute(
        f'ALTER TABLE "{schema}"."{TABLE}" '
        "ADD COLUMN IF NOT EXISTS outstanding NUMERIC(15, 2), "
        "ADD COLUMN IF NOT EXISTS due_date DATE, "
        "ADD COLUMN IF NOT EXISTS outstanding_as_of TIMESTAMP WITHOUT TIME ZONE"
    )


def downgrade() -> None:
    schema = translated_schema(op.get_bind(), SCHEMA)
    op.execute(
        f'ALTER TABLE "{schema}"."{TABLE}" '
        "DROP COLUMN IF EXISTS outstanding_as_of, "
        "DROP COLUMN IF EXISTS due_date, "
        "DROP COLUMN IF EXISTS outstanding"
    )
