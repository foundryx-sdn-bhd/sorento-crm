"""The five REF-PROBES reference kinds get their `chatbot_entity_kinds` rows (release fix
12 Oct 2026, PR #1569 follow-up).

REF-PROBES added `delivery_order`, `sales_order`, `customer_po_ref`, `purchase_order` and
`purchase_request` to `contracts.ENTITY_HINTS` but seeded no policy row for them, so the
AC-1502 contract (one `chatbot_entity_kinds` row per entity hint,
`tests/chatbot/test_rearch_s0_entity_kinds_seed.py`) went red. Each row mirrors `order`:
an exact-match resolver probe, no did-you-mean, narrowing not applicable.

Data only, no schema change. Idempotent: a kind already present is left alone, so bootstrap
may call `insert_kinds(bind)` too. No parser publish. The promotion candidate (`rkind_0002`,
the v56 text) carries no policy blocks and already names the five kinds on its hint line, so
it does not change. A version that renders `{{entity_kinds}}` / `{{entity_kinds_detail}}` from
this table (the `pdyn_0003` lineage, `app/services/chatbot_prompt_vars.py`) gains the five
hint values and five "Entity kind" lines on the next turn after this runs.

Revision ID: rkind_0003_reference_kind_rows
Revises: wc_compat_0001_table
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision = "rkind_0003_reference_kind_rows"
down_revision = "wc_compat_0001_table"
branch_labels = None
depends_on = None

#: Frozen here, not imported: a migration's data must not move when the seed module does.
KIND_ROWS = (
    ("delivery_order", "Delivery order", "orders"),
    ("sales_order", "Sales order", "sales_orders"),
    ("customer_po_ref", "Customer PO ref", "sales_orders"),
    ("purchase_order", "Purchase order", "purchase_orders"),
    ("purchase_request", "Purchase request", "purchase_requests"),
)


def insert_kinds(bind) -> int:
    """Insert each reference kind row that is absent. Returns how many it inserted."""
    inserted = 0
    for kind, label, source in KIND_ROWS:
        present = bind.execute(
            sa.text("SELECT 1 FROM chatbot_entity_kinds WHERE kind = :kind"), {"kind": kind}
        ).first()
        if present is not None:
            continue
        next_order = bind.execute(
            sa.text("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM chatbot_entity_kinds")
        ).scalar()
        bind.execute(
            sa.text(
                "INSERT INTO chatbot_entity_kinds "
                "(id, kind, label, resolver_source, did_you_mean, default_narrowing, family_grouping, "
                "base_property_words, sort_order) "
                "VALUES (:id, :kind, :label, :resolver_source, false, 'not_applicable', NULL, "
                "CAST('{}' AS jsonb), :sort_order)"
            ),
            {
                "id": str(uuid.uuid4()),
                "kind": kind,
                "label": label,
                "resolver_source": source,
                "sort_order": int(next_order or 0),
            },
        )
        inserted += 1
    return inserted


def upgrade() -> None:
    insert_kinds(op.get_bind())


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("DELETE FROM chatbot_entity_kinds WHERE kind = ANY(:kinds)"),
        {"kinds": [kind for kind, _label, _source in KIND_ROWS]},
    )
