"""customer_aliases + customer_merge_map (DEBTOR-CODE-UNIQUE)

Owner decision 4 Oct 2026: one customer per debtor code per company. This migration is
ADDITIVE ONLY: the two tables the merge script (`scripts/merge_customers_by_code.py`) writes
to. It does NOT merge anything and does NOT create the code-unique index; the script does both
after a clean apply, and a follow-up migration adds the index for parity once prod is merged
(card Q4 (a)), so a deploy never depends on a destructive run.

Revision ID: dcu_0001
Revises: ideation_capture_0001
"""
from __future__ import annotations

from alembic import op

revision = "dcu_0001"
down_revision = "ideation_capture_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_aliases (
            id uuid PRIMARY KEY,
            company_id uuid NOT NULL REFERENCES companies(id),
            customer_id uuid NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            alias_name varchar(255) NOT NULL,
            source varchar(32) NOT NULL DEFAULT 'merge',
            merge_run_id varchar(64),
            created_at timestamp NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_customer_aliases_customer_name "
        "ON customer_aliases (customer_id, lower(btrim(alias_name)))"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_customer_aliases_company_name "
        "ON customer_aliases (company_id, lower(btrim(alias_name)))"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_customer_aliases_company_id ON customer_aliases (company_id)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_merge_map (
            id uuid PRIMARY KEY,
            company_id uuid NOT NULL REFERENCES companies(id),
            run_id varchar(64) NOT NULL,
            code_key varchar(100) NOT NULL,
            survivor_id uuid NOT NULL,
            retired_id uuid NOT NULL,
            retired_row jsonb NOT NULL,
            repointed jsonb NOT NULL DEFAULT '{}'::jsonb,
            dropped jsonb NOT NULL DEFAULT '[]'::jsonb,
            updated jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at timestamp NOT NULL DEFAULT now(),
            reverted_at timestamp
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_customer_merge_map_run_id ON customer_merge_map (run_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_customer_merge_map_survivor_id ON customer_merge_map (survivor_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_customer_merge_map_retired_id ON customer_merge_map (retired_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_customer_merge_map_company_id ON customer_merge_map (company_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS customer_merge_map")
    op.execute("DROP TABLE IF EXISTS customer_aliases")
