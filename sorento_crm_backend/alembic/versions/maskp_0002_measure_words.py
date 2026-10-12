"""The sub-ask measures and the words people use for them, as rows of the chatbot glossary
`chatbot_status_words` tagged `sub_ask_measures` (MULTI-ASK-PLAN2, crew decision with the
owner, 12 Oct 2026: vocabulary is data, never prompt text).

The parser prompt reads them through the prompt-vars variable `{{sub_ask_measures}}`
(`chatbot_prompt_vars`), which `SUB_ASKS_ADDENDUM` names in place of a word list. Nothing in
code matches these words: the owner edits them on the Chatbot Status Words page and the
next turn's prompt carries the edit. No row is in domain `order`, whose rows the
`{{order_status_values}}` list renders.

Insert-only and idempotent: `ON CONFLICT (value) DO NOTHING`, so an owner edit made before a
re-run is never overwritten. `apply(bind)` is shared with `scripts.bootstrap_env`.

Revision ID: maskp_0002_measure_words
Revises: maskp_0001_parser_sub_asks
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "maskp_0002_measure_words"
down_revision = "maskp_0001_parser_sub_asks"
branch_labels = None
depends_on = None

PROMPT_LIST = "sub_ask_measures"

# (domain, value, label, trigger words) in prompt order. Frozen here: later changes are the
# owner's, on the Chatbot Status Words page. The words are the vocabulary the r7 prompt text
# carried (owner hand test, 9 Oct 2026).
MEASURE_WORDS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "goods_receive", "received_qty",
        "how many already came INTO the warehouse over a period, whatever the word; never goods still on the way, never incoming",
        ("received", "came in", "goods received", "入库", "进库", "进仓", "进仓库", "入仓",
         "收货", "到了多少", "diterima", "masuk gudang"),
    ),
    (
        "spo_allocation", "last_receipt",
        "when it last came in: the last SPO line's delivery date, with its quantity",
        ("last in", "last received", "last incoming qty", "最后一次进库", "最后一次入库"),
    ),
    (
        "incoming", "incoming", "only goods NOT yet arrived (on the way, ETA); goods that went into the warehouse are received_qty",
        ("incoming", "on the way", "arriving", "ETA", "到货", "在途", "在路上"),
    ),
    ("inventory", "stock", "on hand now", ("stock", "on hand", "库存", "stok")),
    ("purchase_cost", "supplier", "who supplies it", ("supplier", "供应商", "pembekal")),
    (
        "purchase_cost", "last_cost", "the purchase price",
        ("last cost", "purchase price", "cost price", "采购价", "进价", "harga beli"),
    ),
    (
        "sales", "sold_total", "how many were sold in all, in any language; a sale is never other",
        ("sold", "卖了", "卖出", "jual", "jualan", "terjual"),
    ),
    (
        "sales", "sold_by", "how many were sold per group (salesman, customer, ...)",
        ("per salesman", "per customer", "每位业务员", "业务员", "ikut salesman"),
    ),
    (
        "sales", "other", "anything else asked; the label says what",
        ("margin", "profit", "lead time", "毛利", "利润"),
    ),
)


def apply(bind) -> None:
    for i, (domain, value, label, words) in enumerate(MEASURE_WORDS):
        bind.execute(
            sa.text(
                # The id is named: a `create_all`-built table (scripts.bootstrap_env, CI) has
                # no database default for it, only the model's Python-side one.
                "INSERT INTO chatbot_status_words (id, domain, value, label, trigger_words, sort_order, prompt_lists) "
                "VALUES (gen_random_uuid(), :domain, :value, :label, CAST(:words AS text[]), :sort, "
                "CAST(:lists AS text[])) "
                "ON CONFLICT (value) DO NOTHING"
            ),
            {"domain": domain, "value": value, "label": label, "words": list(words), "sort": 100 + i,
             "lists": [PROMPT_LIST]},
        )


def upgrade() -> None:
    apply(op.get_bind())


def downgrade() -> None:
    """Drop the measure rows still tagged for the list only; an owner retag keeps a row."""
    op.get_bind().execute(
        sa.text(
            "DELETE FROM chatbot_status_words WHERE value = ANY(CAST(:values AS text[])) "
            "AND prompt_lists = CAST(:lists AS text[])"
        ),
        {"values": [v for _d, v, _l, _w in MEASURE_WORDS], "lists": [PROMPT_LIST]},
    )
