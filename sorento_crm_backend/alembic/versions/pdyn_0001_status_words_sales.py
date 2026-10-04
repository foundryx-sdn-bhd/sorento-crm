"""Chatbot status words registry + the `sales` domain (PLAN-prompt-dynamic-30sep D6, D9).

1. `chatbot_status_words`: per domain, the status values the parser may emit and the
   customer words that set each one. Seeded from what the parser prompt carried as hand
   lists (the ORDER_STATUS FILTER bullets and the so/do/both addenda) plus the three sales
   statuses, with the owner's sales words (30 Sep 2026). The prompt renders its status
   bullets and value lists from these rows at request time (`{{statuses}}`,
   `{{status_values}}`).
2. A `sales` row in `chatbot_domains` (owner, 30 Sep 2026: "better own sales domain
   otherwise jumble up with order"): the sales report, sales analysis and top selling
   tools, reveal key `sales_orders.sales_report`, escalation `customer_service`, and the
   `order` row's narrowing copied as it stands, because every sales ask ran under `order`
   until now and must narrow exactly as it did.

Additive and idempotent: `IF NOT EXISTS` on the table, `ON CONFLICT DO NOTHING` on every
seed, so an owner edit made before a re-run is never overwritten. `apply(bind)` is shared
with `scripts.bootstrap_env` (a `create_all`-built database never runs this body).

Revision ID: pdyn_0001_status_words_sales
Revises: ideation_capture_0001
Create Date: 2026-09-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "pdyn_0001_status_words_sales"
down_revision = "ideation_capture_0001"
branch_labels = None
depends_on = None


# (domain, value, label, trigger words) in prompt order. Frozen here: later changes are
# the owner's, on the Chatbot Status Words page.
STATUS_WORDS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "order", "outstanding", "orders NOT yet delivered",
        ("outstanding", "pending", "not delivered", "undelivered", "not yet delivered",
         "still pending", "open orders", "belum hantar", "belum sampai", "tak hantar lagi"),
    ),
    (
        "order", "delivered", "orders ALREADY delivered",
        ("delivered", "completed", "done", "sudah hantar", "sudah sampai"),
    ),
    (
        "order", "so_outstanding", "a sales order with NO delivery order yet",
        ("SO outstanding", "outstanding SO", "outstanding sales order", "ordered but no DO",
         "no DO yet", "belum DO", "belum keluar DO", "还没出DO", "未出DO"),
    ),
    (
        "order", "do_outstanding", "a delivery order raised and not yet delivered",
        ("delivery order outstanding", "DO outstanding", "pending delivery order",
         "pending DO", "DO pending"),
    ),
    (
        "order", "outstanding_both", "both the SO backlog and the DO pending",
        ("both SO and DO", "sales order and delivery order outstanding"),
    ),
    (
        "sales", "sales_report", "a customer's or product's sales figures by month",
        ("sales", "sales report", "sales performance", "sales figures", "jualan report",
         "laporan jualan", "销售报告"),
    ),
    (
        "sales", "sales_analysis", "the company's own sales totals",
        ("sales analysis", "total sales", "dealer sales", "project sales", "sales by month",
         "monthly sales", "jualan tahun ini", "销售总额"),
    ),
    (
        "sales", "top_selling", "a ranking of items by what was sold",
        ("top selling", "best selling", "hot selling", "most sold", "top sellers",
         "best seller", "barang paling laku", "paling laris", "畅销"),
    ),
)

SALES_DOMAIN = {
    "name": "sales",
    "label": "sales",
    "intents": ["check_sales"],
    "tools": ["crm_sales_report", "crm_sales_analysis", "crm_top_selling_report"],
    "escalation_team_code": "customer_service",
    "switch_words": ["sales", "sales report", "top selling", "sales analysis", "best selling"],
    "reveal_key": "sales_orders.sales_report",
}


def create_table(bind) -> None:
    bind.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS chatbot_status_words (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                domain text NOT NULL,
                value text NOT NULL UNIQUE,
                label text NOT NULL,
                trigger_words text[] NOT NULL DEFAULT '{}',
                sort_order integer NOT NULL DEFAULT 0,
                created_at timestamp without time zone NOT NULL DEFAULT now(),
                updated_at timestamp without time zone NOT NULL DEFAULT now()
            )
            """
        )
    )


def seed_status_words(bind) -> None:
    for i, (domain, value, label, words) in enumerate(STATUS_WORDS):
        bind.execute(
            sa.text(
                # The id is named: a `create_all`-built table (scripts.bootstrap_env, CI) has
                # no database default for it, only the model's Python-side one.
                "INSERT INTO chatbot_status_words (id, domain, value, label, trigger_words, sort_order) "
                "VALUES (gen_random_uuid(), :domain, :value, :label, CAST(:words AS text[]), :sort) "
                "ON CONFLICT (value) DO NOTHING"
            ),
            {"domain": domain, "value": value, "label": label, "words": list(words), "sort": i},
        )


def insert_sales_domain(bind) -> None:
    row = SALES_DOMAIN
    bind.execute(
        sa.text(
            "INSERT INTO chatbot_domains (id, name, label, intents, tools, primary_tool, "
            "escalation_team_code, switch_words, narrowing, takes_date_filter, reveal_key, "
            "supported, ladder, sort_order) "
            "SELECT gen_random_uuid(), :name, :label, CAST(:intents AS text[]), "
            "CAST(:tools AS text[]), NULL, :team, CAST(:words AS text[]), "
            "COALESCE((SELECT narrowing FROM chatbot_domains WHERE name = 'order'), '{}'::jsonb), "
            "true, :reveal, true, '{}', "
            "COALESCE((SELECT max(sort_order) + 1 FROM chatbot_domains), 0) "
            "WHERE NOT EXISTS (SELECT 1 FROM chatbot_domains WHERE name = :name)"
        ),
        {
            "name": row["name"],
            "label": row["label"],
            "intents": row["intents"],
            "tools": row["tools"],
            "team": row["escalation_team_code"],
            "words": row["switch_words"],
            "reveal": row["reveal_key"],
        },
    )


def apply(bind) -> None:
    create_table(bind)
    seed_status_words(bind)
    insert_sales_domain(bind)


def upgrade() -> None:
    apply(op.get_bind())


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM chatbot_domains WHERE name = 'sales'"))
    bind.execute(sa.text("DROP TABLE IF EXISTS chatbot_status_words"))
