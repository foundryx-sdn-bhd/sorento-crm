"""Order inquiry: a table remembering received links released to a CS reserve.

`PLAN-oi-reserve-received.md` item 1. Additive, no backfill, `IF NOT EXISTS` guarded
(the shared dev database converges through `create_all`, see `sorento_crm_backend/CLAUDE.md`).

Revision ID: oirr_0001_released_links
Revises: dobs_0001_assume_delivered
Create Date: 2026-10-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "oirr_0001_released_links"
down_revision = "dobs_0001_assume_delivered"
branch_labels = None
depends_on = None


def _schema(bind, module: str) -> str:
    """Same helper as `oisl_0001_suggested_links.py` (scratch-schema aware raw SQL)."""
    current = bind.exec_driver_sql("SELECT current_schema()").scalar()
    return module if current in (None, "public") else f"{current}_{module}"


def upgrade() -> None:
    bind = op.get_bind()
    projects = _schema(bind, "projects")
    bind.execute(
        sa.text(
            f"""
            CREATE TABLE IF NOT EXISTS "{projects}".order_inquiry_released_links (
                id UUID PRIMARY KEY,
                company_id UUID REFERENCES companies(id),
                row_id UUID NOT NULL
                    REFERENCES "{projects}".order_inquiry_rows(id) ON DELETE CASCADE,
                reserve_request_row_id UUID
                    CONSTRAINT fk_order_inquiry_released_links_reserve_request_row
                    REFERENCES "{projects}".order_inquiry_reserve_request_rows(id)
                    ON DELETE SET NULL,
                po_line_id UUID REFERENCES purchase_order_lines(id) ON DELETE CASCADE,
                spo_allocation_id UUID
                    CONSTRAINT fk_order_inquiry_released_links_spo_allocation
                    REFERENCES spo_allocations(id) ON DELETE CASCADE,
                document VARCHAR(80),
                qty NUMERIC(15, 4) NOT NULL
                    CONSTRAINT ck_order_inquiry_released_links_qty_positive CHECK (qty > 0),
                released_by VARCHAR(100) REFERENCES users(id) ON DELETE SET NULL,
                released_at TIMESTAMP NOT NULL DEFAULT now(),
                created_at TIMESTAMP NOT NULL DEFAULT now(),
                CONSTRAINT ck_order_inquiry_released_links_one_target
                    CHECK ((po_line_id IS NOT NULL)::int
                           + (spo_allocation_id IS NOT NULL)::int = 1)
            );
            CREATE INDEX IF NOT EXISTS ix_order_inquiry_released_links_company_id
                ON "{projects}".order_inquiry_released_links (company_id);
            CREATE INDEX IF NOT EXISTS ix_order_inquiry_released_links_row
                ON "{projects}".order_inquiry_released_links (row_id);
            CREATE INDEX IF NOT EXISTS ix_order_inquiry_released_links_po_line
                ON "{projects}".order_inquiry_released_links (po_line_id);
            CREATE INDEX IF NOT EXISTS ix_order_inquiry_released_links_spo_allocation
                ON "{projects}".order_inquiry_released_links (spo_allocation_id);
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    projects = _schema(bind, "projects")
    bind.execute(sa.text(f'DROP TABLE IF EXISTS "{projects}".order_inquiry_released_links'))
