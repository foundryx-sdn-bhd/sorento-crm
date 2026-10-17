"""OI-LINE-SPLIT S1: `projects.so_line_splits`, one SO line split into deliveries.

Plan: documentation/plans/scm/PLAN-oi-line-split-16oct.md ("Build design (final)", Storage).
Each row is one split of an SO line: qty, delivery date (NULL for a cancel), the line it
sits on (staff-picked; NULL = a new line not in AutoCount yet), remark, and the base the
split was made against. A line with no rows is not split.

Additive only and idempotent (`IF NOT EXISTS`) so a DB that converged through `create_all`
can take it in any order. Downgrade drops the table.

Revision ID: ols_0001_so_line_splits
Revises: sr_0001_sample_requests
Create Date: 2026-10-17
"""
import sqlalchemy as sa
from alembic import op

# Kept <=32 chars: alembic_version.version_num is varchar(32).
revision = "ols_0001_so_line_splits"
down_revision = "sr_0001_sample_requests"
branch_labels = None
depends_on = None


def _schema(bind, module: str) -> str:
    """Where a module's tables live, for RAW SQL, under BOTH substrates (the helper
    `sds_0001_stock_debt_snapshots` carries: the test suite builds its scratch schema as
    `<default>_projects`)."""
    current = bind.exec_driver_sql("SELECT current_schema()").scalar()
    return module if current in (None, "public") else f"{current}_{module}"


def _upgrade_sql(projects: str) -> tuple:
    return (
        f'CREATE SCHEMA IF NOT EXISTS "{projects}"',
        f"""
        CREATE TABLE IF NOT EXISTS "{projects}".so_line_splits (
            id UUID PRIMARY KEY,
            company_id UUID NOT NULL REFERENCES public.companies(id),
            project_sales_order_id UUID NOT NULL
                REFERENCES "{projects}".sales_orders(id) ON DELETE CASCADE,
            so_line_id UUID NOT NULL
                REFERENCES "{projects}".sales_order_lines(id) ON DELETE CASCADE,
            seq INTEGER NOT NULL DEFAULT 0,
            qty NUMERIC(15, 4) NOT NULL,
            delivery_date DATE,
            is_cancel BOOLEAN NOT NULL DEFAULT false,
            sits_on_line_id UUID
                REFERENCES "{projects}".sales_order_lines(id) ON DELETE SET NULL,
            remark TEXT,
            base_qty NUMERIC(15, 4) NOT NULL,
            base_date DATE,
            created_by VARCHAR(100) REFERENCES public.users(id) ON DELETE SET NULL,
            updated_by VARCHAR(100) REFERENCES public.users(id) ON DELETE SET NULL,
            created_at TIMESTAMP NOT NULL DEFAULT now(),
            updated_at TIMESTAMP NOT NULL DEFAULT now(),
            CONSTRAINT ck_so_line_splits_qty_positive CHECK (qty > 0),
            CONSTRAINT ck_so_line_splits_date_iff_not_cancel CHECK (
                (is_cancel AND delivery_date IS NULL)
                OR (NOT is_cancel AND delivery_date IS NOT NULL)
            )
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_projects_so_line_splits_company_id "
        f'ON "{projects}".so_line_splits (company_id)',
        "CREATE INDEX IF NOT EXISTS ix_so_line_splits_order "
        f'ON "{projects}".so_line_splits (project_sales_order_id)',
        "CREATE INDEX IF NOT EXISTS ix_so_line_splits_line "
        f'ON "{projects}".so_line_splits (so_line_id)',
    )


def upgrade() -> None:
    bind = op.get_bind()
    for statement in _upgrade_sql(_schema(bind, "projects")):
        bind.execute(sa.text(statement))


def downgrade() -> None:
    bind = op.get_bind()
    projects = _schema(bind, "projects")
    bind.execute(sa.text(f'DROP TABLE IF EXISTS "{projects}".so_line_splits'))
