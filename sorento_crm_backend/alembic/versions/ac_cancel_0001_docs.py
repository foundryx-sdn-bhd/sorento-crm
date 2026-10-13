"""AutoCount Cancel SO / Cancel PO (AC-CANCEL-CRM, contract 2.8).

Plan: documentation/plans/autocount/PLAN-autocount-cancel-crm-13oct.md.

1. `autocount_cancel_docs` / `autocount_cancel_lines`: the CRM's copy of AutoCount XS / XP
   documents, keyed (company, kind, book, DocKey) and (doc, DtlKey); each line carries the SO /
   PO / SPO line it was matched to, or the reason it was not.
2. `qty_cancelled` on `sales_order_lines`, `purchase_order_lines` and `spo_allocations`: derived
   from (1) on every sync; AutoCount's own ordered and TransferedQty columns are untouched.
   `cancelled_by_cancel_doc` on SO / PO lines: set while a line is `cancelled` BECAUSE cancel
   documents cancelled all of it, so only such a line ever reopens when the cancel lowers.
3. The ingest slugs `scm.{cancel_sales_orders,cancel_purchase_orders,consignments,
   consignment_returns}.{view,edit,delete}`, granted to admin / superadmin and to every role
   holding the matching order slug (`scm.sales_orders.*`, or `scm.purchase_orders.*` for
   Cancel PO), the feed's role: view / edit follow the order's `.edit`, the sweep (`.delete`)
   follows the order's `.delete` (D30: removing is its own permission). Consignment documents
   are stored only (crew, 13 Oct).

Additive only: new tables, and NOT NULL columns with a server default of 0, so no existing row
changes meaning. Downgrade drops everything this adds.

Revision ID: ac_cancel_0001_docs
Revises: spec_in_0001_stage_label
Create Date: 2026-10-13
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "ac_cancel_0001_docs"
down_revision = "spec_in_0001_stage_label"
branch_labels = None
depends_on = None

_UUID = postgresql.UUID(as_uuid=False)
_JSON = postgresql.JSONB(astext_type=sa.Text())
_QTY_TABLES = ("sales_order_lines", "purchase_order_lines", "spo_allocations")
_FLAG_TABLES = ("sales_order_lines", "purchase_order_lines")
# (entity, the order slug whose holders are the feed's role)
_SLUG_ENTITIES = (("cancel_sales_orders", "scm.sales_orders.edit", "AutoCount Cancel SO"),
                  ("cancel_purchase_orders", "scm.purchase_orders.edit", "AutoCount Cancel PO"),
                  ("consignments", "scm.sales_orders.edit", "AutoCount Consignment"),
                  ("consignment_returns", "scm.sales_orders.edit",
                   "AutoCount Consignment Return"))
_ACTIONS = (("view", "View"), ("edit", "Ingest"), ("delete", "Sweep"))


def seed_permissions(bind) -> None:
    """Create the slugs when absent and grant them. Idempotent: running it again adds no row."""
    for entity, feed_slug, label in _SLUG_ENTITIES:
        slugs = [f"scm.{entity}.{action}" for action, _ in _ACTIONS]
        for (action, verb), slug in zip(_ACTIONS, slugs):
            bind.execute(
                sa.text(
                    """
                    INSERT INTO user_permissions (id, slug, name, description, created_at)
                    SELECT gen_random_uuid()::text, :slug, :name, :descr, now()
                    WHERE NOT EXISTS (SELECT 1 FROM user_permissions WHERE slug = :slug)
                    """
                ),
                {"slug": slug, "name": f"{verb} {label}",
                 "descr": f"AutoCount ingest door for {label} documents ({action})."},
            )
        bind.execute(
            sa.text(
                """
                INSERT INTO user_role_permissions (id, role_id, permission_id, assigned_at)
                SELECT gen_random_uuid()::text, r.id, p.id, now()
                FROM user_roles r
                CROSS JOIN user_permissions p
                WHERE r.slug IN ('admin', 'superadmin') AND p.slug = ANY(:slugs)
                ON CONFLICT (role_id, permission_id) DO NOTHING
                """
            ),
            {"slugs": slugs},
        )
        # D30 holds: `.view` / `.edit` follow the order's `.edit`, the sweep (`.delete`) follows
        # the order's own `.delete`, never its edit.
        for door, feed in ((slugs[:2], feed_slug), (slugs[2:], feed_slug[: -len("edit")] + "delete")):
            bind.execute(
                sa.text(
                    """
                    INSERT INTO user_role_permissions (id, role_id, permission_id, assigned_at)
                    SELECT gen_random_uuid()::text, held.role_id, p.id, now()
                    FROM user_role_permissions held
                    JOIN user_permissions feed
                      ON feed.id = held.permission_id AND feed.slug = :feed
                    CROSS JOIN user_permissions p
                    WHERE p.slug = ANY(:slugs)
                    ON CONFLICT (role_id, permission_id) DO NOTHING
                    """
                ),
                {"slugs": door, "feed": feed},
            )


def upgrade() -> None:
    op.create_table(
        "autocount_cancel_docs",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("company_id", _UUID, sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("source_book", sa.String(20), nullable=False),
        sa.Column("doc_key", sa.BigInteger(), nullable=False),
        sa.Column("doc_no", sa.String(50), nullable=False),
        sa.Column("doc_date", sa.Date(), nullable=False),
        sa.Column("is_void", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("source_modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_vanished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_record", _JSON, nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=False), server_default=sa.func.now(),
                  nullable=False),
    )
    op.create_index("ix_autocount_cancel_docs_company_id", "autocount_cancel_docs", ["company_id"])
    op.create_index(
        "uq_autocount_cancel_docs_identity",
        "autocount_cancel_docs",
        ["company_id", "kind", "source_book", "doc_key"],
        unique=True,
    )

    op.create_table(
        "autocount_cancel_lines",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("company_id", _UUID, sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("cancel_doc_id", _UUID,
                  sa.ForeignKey("autocount_cancel_docs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dtl_key", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=True),
        sa.Column("item_code", sa.String(100), nullable=True),
        sa.Column("location_code", sa.String(50), nullable=True),
        sa.Column("qty", sa.Numeric(15, 4), nullable=False, server_default="0"),
        sa.Column("from_doc_type", sa.String(10), nullable=True),
        sa.Column("from_doc_no", sa.String(100), nullable=True),
        sa.Column("from_dtl_key", sa.BigInteger(), nullable=True),
        sa.Column("match_status", sa.String(20), nullable=False, server_default="waiting"),
        sa.Column("match_reason", sa.String(40), nullable=True),
        sa.Column("candidate_line_ids", _JSON, nullable=True),
        sa.Column("sales_order_line_id", _UUID,
                  sa.ForeignKey("sales_order_lines.id", ondelete="SET NULL"), nullable=True),
        sa.Column("purchase_order_line_id", _UUID,
                  sa.ForeignKey("purchase_order_lines.id", ondelete="SET NULL"), nullable=True),
        sa.Column("spo_allocation_id", _UUID,
                  sa.ForeignKey("spo_allocations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=False), server_default=sa.func.now(),
                  nullable=False),
    )
    op.create_index("ix_autocount_cancel_lines_company_id", "autocount_cancel_lines",
                    ["company_id"])
    op.create_index("uq_autocount_cancel_lines_doc_dtl", "autocount_cancel_lines",
                    ["cancel_doc_id", "dtl_key"], unique=True)
    op.create_index("ix_autocount_cancel_lines_so_line", "autocount_cancel_lines",
                    ["sales_order_line_id"])
    op.create_index("ix_autocount_cancel_lines_po_line", "autocount_cancel_lines",
                    ["purchase_order_line_id"])
    op.create_index("ix_autocount_cancel_lines_spo", "autocount_cancel_lines",
                    ["spo_allocation_id"])
    op.create_index("ix_autocount_cancel_lines_status", "autocount_cancel_lines",
                    ["company_id", "match_status"])

    for table in _QTY_TABLES:
        op.add_column(
            table,
            sa.Column("qty_cancelled", sa.Numeric(15, 4), nullable=False, server_default="0"),
        )
    for table in _FLAG_TABLES:
        op.add_column(
            table,
            sa.Column("cancelled_by_cancel_doc", sa.Boolean(), nullable=False,
                      server_default=sa.text("false")),
        )
    seed_permissions(op.get_bind())


def downgrade() -> None:
    bind = op.get_bind()
    slugs = [f"scm.{entity}.{action}" for entity, _, _ in _SLUG_ENTITIES for action, _ in _ACTIONS]
    bind.execute(
        sa.text(
            "DELETE FROM user_role_permissions WHERE permission_id IN "
            "(SELECT id FROM user_permissions WHERE slug = ANY(:slugs))"
        ),
        {"slugs": slugs},
    )
    bind.execute(sa.text("DELETE FROM user_permissions WHERE slug = ANY(:slugs)"),
                 {"slugs": slugs})
    for table in _FLAG_TABLES:
        op.drop_column(table, "cancelled_by_cancel_doc")
    for table in _QTY_TABLES:
        op.drop_column(table, "qty_cancelled")
    op.drop_table("autocount_cancel_lines")
    op.drop_table("autocount_cancel_docs")
