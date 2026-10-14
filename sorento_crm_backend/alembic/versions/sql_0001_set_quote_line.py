"""A product set is ONE quotation line (SET-QUOTE-LINE).

Plan: documentation/plans/project-sales/PLAN-set-quote-line-14oct.md.

1. `projects.quotation_lines.product_set_id` (FK product_sets, SET NULL) and
   `set_parts_snapshot` (JSONB, the parts frozen at pick time), a CHECK that a line names a
   product OR a set never both, and an index on the set id.
2. `projects.series_sets`: what a series sells a set for, same shape as `series_products`.

Additive only: nullable columns and a new table, so no existing row changes meaning.
Downgrade drops them.

Revision ID: sql_0001_set_quote_line
Revises: ac_consign_0001_doc_type
Create Date: 2026-10-14
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "sql_0001_set_quote_line"
down_revision = "ac_consign_0001_doc_type"
branch_labels = None
depends_on = None

_FK = "fk_quotation_lines_product_set_id"
_CHECK = "ck_quotation_lines_one_target"
_INDEX = "ix_project_quotation_lines_product_set"


def upgrade() -> None:
    op.add_column(
        "quotation_lines",
        sa.Column("product_set_id", postgresql.UUID(as_uuid=False), nullable=True),
        schema="projects",
    )
    op.add_column(
        "quotation_lines",
        sa.Column("set_parts_snapshot", postgresql.JSONB(), nullable=True),
        schema="projects",
    )
    op.create_foreign_key(
        _FK,
        "quotation_lines",
        "product_sets",
        ["product_set_id"],
        ["id"],
        source_schema="projects",
        referent_schema="public",
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        _CHECK,
        "quotation_lines",
        "NOT (product_id IS NOT NULL AND product_set_id IS NOT NULL)",
        schema="projects",
    )
    op.create_index(_INDEX, "quotation_lines", ["product_set_id"], schema="projects")

    op.create_table(
        "series_sets",
        sa.Column("series_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("product_set_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("selling_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("max_discount_pct", sa.Numeric(5, 2), nullable=True),
        sa.ForeignKeyConstraint(["series_id"], ["projects.series.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_set_id"], ["public.product_sets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("series_id", "product_set_id"),
        schema="projects",
    )


def downgrade() -> None:
    op.drop_table("series_sets", schema="projects")
    op.drop_index(_INDEX, table_name="quotation_lines", schema="projects")
    op.drop_constraint(_CHECK, "quotation_lines", schema="projects", type_="check")
    op.drop_constraint(_FK, "quotation_lines", schema="projects", type_="foreignkey")
    op.drop_column("quotation_lines", "set_parts_snapshot", schema="projects")
    op.drop_column("quotation_lines", "product_set_id", schema="projects")
