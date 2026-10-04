"""AutoCount compare mappings: the saved sheet + column mapping per workbook kind
(DO-COMPARE-SIM, PLAN-do-compare-mapping.md).

Additive: one new global table (no company_id) and its two seed rows, `order_listing` and
`order_tracking`, both on sheet `Master`. Idempotent seed (`ON CONFLICT (kind) DO NOTHING`).

Revision ID: dcm_0001_compare_mappings
Revises: esc1_0001_escalation_allowed
"""
from __future__ import annotations

import json
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "dcm_0001_compare_mappings"
down_revision = "esc1_0001_escalation_allowed"
branch_labels = None
depends_on = None

_SEEDS = {
    "order_listing": [
        ("Doc No", "text", "doc_no"), ("Doc Date", "date", "doc_date"),
        ("Item Code", "text", "item_code"), ("Location", "text", "location"),
        ("Qty", "number", "qty"), ("Unit Price", "money", "unit_price"),
        ("Discount", "percent_text", "discount"), ("Total (Ex)", "money", "total_ex"),
    ],
    "order_tracking": [
        ("Doc. No.", "text", "doc_no"), ("Date", "date", "doc_date"),
        ("Debtor Code", "text", "debtor_code"), ("Cancel", "cancel_flag", "cancel"),
    ],
}


def upgrade() -> None:
    op.create_table(
        "autocount_compare_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("sheet_name", sa.String(100), nullable=False),
        sa.Column("columns", postgresql.JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.UniqueConstraint("kind", name="autocount_compare_mappings_kind_key"),
    )
    bind = op.get_bind()
    for kind, triples in _SEEDS.items():
        columns = [{"excel_header": h, "transform": t, "field": f} for h, t, f in triples]
        bind.execute(
            sa.text(
                "INSERT INTO autocount_compare_mappings (id, kind, sheet_name, columns) "
                "VALUES (:id, :kind, 'Master', CAST(:columns AS jsonb)) "
                "ON CONFLICT (kind) DO NOTHING"
            ),
            {"id": str(uuid.uuid4()), "kind": kind, "columns": json.dumps(columns)},
        )


def downgrade() -> None:
    op.drop_table("autocount_compare_mappings")
