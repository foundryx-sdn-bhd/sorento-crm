"""SO-CANCEL-LINE: who cancelled a sales order line from the fulfilment board, when, and why.

Additive: three nullable columns on `sales_order_lines`. `cancelled_at` is also what the
AutoCount ingest reads to keep such a line cancelled on a re-push.

Revision ID: scl_0001_so_line_cancel_audit
Revises: qr_0001_chat_histories_turn_link
Create Date: 2026-10-07
"""
from alembic import op
import sqlalchemy as sa

revision = "scl_0001_so_line_cancel_audit"
down_revision = "qr_0001_chat_histories_turn_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sales_order_lines", sa.Column("cancelled_at", sa.DateTime(), nullable=True))
    op.add_column(
        "sales_order_lines",
        sa.Column("cancelled_by_id", sa.String(length=100), nullable=True),
    )
    op.add_column("sales_order_lines", sa.Column("cancel_reason", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_sales_order_lines_cancelled_by_id_users",
        "sales_order_lines",
        "users",
        ["cancelled_by_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_sales_order_lines_cancelled_by_id_users", "sales_order_lines", type_="foreignkey"
    )
    op.drop_column("sales_order_lines", "cancel_reason")
    op.drop_column("sales_order_lines", "cancelled_by_id")
    op.drop_column("sales_order_lines", "cancelled_at")
