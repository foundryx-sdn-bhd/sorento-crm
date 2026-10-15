"""STOCK-DEBT-SNAPSHOT: the frozen Stock Debt board and its per-product decisions.

Additive only, idempotent (`IF NOT EXISTS`) so the shared dev DB, which converges through
`create_all`, can take it in any order. The same DDL ships as the crew SQL at
`crew/state/migrations/STOCK-DEBT-SNAPSHOT.sql`.
Plan: documentation/plans/scm/PLAN-stock-debt-snapshot-15oct.md.

Revision ID: sds_0001_stock_debt_snapshots
Revises: sf_0001_series_brands
"""
import sqlalchemy as sa
from alembic import op

# Kept <=32 chars: alembic_version.version_num is varchar(32).
revision = "sds_0001_stock_debt_snapshots"
down_revision = "sf_0001_series_brands"
branch_labels = None
depends_on = None


UPGRADE_SQL = (
    """
    CREATE TABLE IF NOT EXISTS stock_debt_snapshots (
        id UUID PRIMARY KEY,
        company_id UUID NOT NULL REFERENCES companies(id),
        name VARCHAR(120) NOT NULL,
        filters JSONB NOT NULL,
        months JSONB NOT NULL,
        tba_month VARCHAR(10),
        totals JSONB NOT NULL,
        row_count INTEGER NOT NULL DEFAULT 0,
        created_by VARCHAR(100) REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMP NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_stock_debt_snapshots_company_created "
    "ON stock_debt_snapshots (company_id, created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS stock_debt_snapshot_rows (
        id UUID PRIMARY KEY,
        company_id UUID NOT NULL REFERENCES companies(id),
        snapshot_id UUID NOT NULL REFERENCES stock_debt_snapshots(id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        product_id UUID REFERENCES products(id) ON DELETE SET NULL,
        product_code VARCHAR(180) NOT NULL,
        product_name TEXT,
        "row" JSONB NOT NULL,
        lines JSONB NOT NULL,
        decision VARCHAR(16),
        decision_warehouse_id UUID REFERENCES warehouses(id) ON DELETE SET NULL,
        decision_note TEXT,
        decided_by VARCHAR(100) REFERENCES users(id) ON DELETE SET NULL,
        decided_at TIMESTAMP,
        CONSTRAINT ck_stock_debt_snapshot_rows_decision
            CHECK (decision IS NULL OR decision IN ('reserve', 'buy', 'ignore'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_stock_debt_snapshot_rows_snapshot_position "
    "ON stock_debt_snapshot_rows (snapshot_id, position)",
    "CREATE INDEX IF NOT EXISTS ix_stock_debt_snapshot_rows_snapshot_decision "
    "ON stock_debt_snapshot_rows (snapshot_id, decision)",
    "CREATE INDEX IF NOT EXISTS ix_stock_debt_snapshot_rows_company_id "
    "ON stock_debt_snapshot_rows (company_id)",
)


def upgrade() -> None:
    bind = op.get_bind()
    for statement in UPGRADE_SQL:
        bind.execute(sa.text(statement))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS stock_debt_snapshot_rows")
    op.execute("DROP TABLE IF EXISTS stock_debt_snapshots")
