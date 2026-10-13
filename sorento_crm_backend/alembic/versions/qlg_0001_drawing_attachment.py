"""The technical drawing a quotation line goes out with (QUOTATION-UX, AC-QLG08 / AC-QLG10).

`projects.quotation_lines.drawing_attachment_id` is the drawing twin of `image_attachment_id`:
the "Technical Specifications" image a salesperson picked for the line, or, once the version is
issued, the one the customer was sent (the issue freeze fills it, never overwrites it). Nullable,
because a draft line with no pick resolves its drawing live off the product.

ON DELETE SET NULL, like `image_attachment_id`: hard-deleting a file must not take the quotation
line with it.

No backfill: nothing records which drawing a line was quoted with before this column, so every
existing row starts NULL and reads the product's first drawing live, which is what the screen
showed before.

Revision ID: qlg_0001_drawing_attachment
Revises: spec_in_0001_stage_label
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "qlg_0001_drawing_attachment"
down_revision = "spec_in_0001_stage_label"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "quotation_lines",
        sa.Column("drawing_attachment_id", UUID(as_uuid=False), nullable=True),
        schema="projects",
    )
    op.create_foreign_key(
        "fk_projects_quotation_lines_drawing_attachment",
        "quotation_lines",
        "attachments",
        ["drawing_attachment_id"],
        ["id"],
        source_schema="projects",
        referent_schema="public",
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_projects_quotation_lines_drawing_attachment",
        "quotation_lines",
        schema="projects",
        type_="foreignkey",
    )
    op.drop_column("quotation_lines", "drawing_attachment_id", schema="projects")
