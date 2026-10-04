"""Owner answers of 2 Oct 2026 to the PROMPT-DYNAMIC per-list table (PLAN-prompt-dynamic-30sep,
"Owner answers"): the parser prompt's hand lists become variables that render his exact text.

1. D-B4: `chatbot_status_words.prompt_lists` (text[]), the parser prompt lists a status row
   is in. His text keeps subsets: the status bullets and the `order_status` values list only
   `outstanding` and `delivered`; the `status` values add `sales_report`. Seeded on those
   three rows only, and only where a row has no lists yet, so an owner edit stands.
2. D-B2: `chatbot_domain_words`, the curated DOMAIN IN MESSAGE word list, seeded with the
   owner's 19 words in his order (`ON CONFLICT DO NOTHING`).

Additive and idempotent. `apply(bind)` is shared with `scripts.bootstrap_env`.

Chained right after pdyn_0001 and BEFORE the wording-layer migrations (pdyn_0002,
pdyn_0003): they call the renderers, which read the column and table this adds (reviewer
pass 3, B1).

Revision ID: pdyn_0004_prompt_lists
Revises: pdyn_0001_status_words_sales
Create Date: 2026-10-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "pdyn_0004_prompt_lists"
down_revision = "pdyn_0001_status_words_sales"
branch_labels = None
depends_on = None

#: The parser prompt lists a status row can be in; the variable of the same name renders
#: the rows tagged with it.
PROMPT_LISTS = ("statuses", "status_values", "status_field_values")

#: Frozen here: later changes are the owner's.
STATUS_PROMPT_LISTS: dict[str, tuple[str, ...]] = {
    "outstanding": ("statuses", "status_values", "status_field_values"),
    "delivered": ("statuses", "status_values", "status_field_values"),
    "sales_report": ("status_field_values",),
}

#: The owner's DOMAIN IN MESSAGE words, in his order (production text of 1 Oct 2026, line 87).
DOMAIN_WORDS: tuple[str, ...] = (
    "stock", "incoming", "ETA", "delivery", "order", "outstanding", "DO", "SO", "PO",
    "purchase cost", "promo", "price", "spec", "photo", "catalogue", "certificate", "forms",
    "shipment", "GRN",
)


def apply(bind) -> None:
    bind.execute(
        sa.text(
            "ALTER TABLE chatbot_status_words "
            "ADD COLUMN IF NOT EXISTS prompt_lists text[] NOT NULL DEFAULT '{}'"
        )
    )
    for value, lists in STATUS_PROMPT_LISTS.items():
        bind.execute(
            sa.text(
                "UPDATE chatbot_status_words SET prompt_lists = CAST(:lists AS text[]) "
                "WHERE value = :value AND prompt_lists = '{}'"
            ),
            {"value": value, "lists": list(lists)},
        )
    bind.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS chatbot_domain_words (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                word text NOT NULL UNIQUE,
                sort_order integer NOT NULL DEFAULT 0,
                created_at timestamp without time zone NOT NULL DEFAULT now(),
                updated_at timestamp without time zone NOT NULL DEFAULT now()
            )
            """
        )
    )
    for i, word in enumerate(DOMAIN_WORDS):
        bind.execute(
            sa.text(
                # The id is named: a `create_all`-built table has no database default for it.
                "INSERT INTO chatbot_domain_words (id, word, sort_order) "
                "VALUES (gen_random_uuid(), :word, :sort) ON CONFLICT (word) DO NOTHING"
            ),
            {"word": word, "sort": i},
        )


def upgrade() -> None:
    apply(op.get_bind())


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DROP TABLE IF EXISTS chatbot_domain_words"))
    bind.execute(sa.text("ALTER TABLE chatbot_status_words DROP COLUMN IF EXISTS prompt_lists"))
